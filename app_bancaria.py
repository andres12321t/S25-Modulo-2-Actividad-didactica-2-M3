import argparse
import json
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List
from urllib.parse import urlparse

from simulacion_bancaria import (
    ACTION_PROBABILITIES,
    CATEGORY_NAMES,
    CATEGORY_PROBABILITIES,
    SCENARIOS,
    ReplicationResult,
    Scenario,
    combine_metrics,
    descriptive_stats,
    estimate_mm1,
    paired_wait_difference_stats,
    run_experiment,
    write_charts,
    write_replications_csv,
    write_summary_csv,
)


ROOT = Path(__file__).resolve().parent
HTML_PATH = ROOT / "interfaz_bancaria.html"
OUTPUT_PREFIX = ROOT / "resultados_simulacion"
SIMULATION_LOCK = threading.Lock()
MAX_REPLICATES = 500
DOWNLOADS = {
    "resultados_simulacion_resumen.csv",
    "resultados_simulacion_replicas.csv",
    "resultados_simulacion_espera_escenarios.svg",
    "resultados_simulacion_espera_tipos.svg",
}


def _stats(values) -> Dict[str, float]:
    average, deviation, lower, upper = descriptive_stats(values)
    return {
        "mean": average,
        "sd": deviation,
        "low": lower,
        "high": upper,
    }


def _metric_summary(
    replications: List[ReplicationResult],
    metric_getter,
) -> Dict[str, float]:
    return _stats(metric_getter(result) for result in replications)


def _cashier_summaries(
    scenario: Scenario,
    replications: List[ReplicationResult],
) -> List[dict]:
    if scenario.is_mixed:
        labels = ["Mixto"] * scenario.mixed_cashiers
    else:
        labels = (
            ["Retiro"] * scenario.withdrawal_cashiers
            + ["Consignación o pago"] * scenario.payment_cashiers
        )

    summaries = []
    for index, label in enumerate(labels):
        metrics = [result.cashiers[index] for result in replications]
        totals = combine_metrics(metrics)
        queueing = estimate_mm1(metrics)
        summaries.append({
            "cashier": index + 1,
            "action": label,
            "usersPerDay": totals.customers / len(replications),
            "service": _metric_summary(
                replications,
                lambda result, i=index: result.cashiers[i].average_service_minutes,
            ),
            "wait": _metric_summary(
                replications,
                lambda result, i=index: result.cashiers[i].average_wait_minutes,
            ),
            "utilizationPct": _metric_summary(
                replications,
                lambda result, i=index: result.cashiers[i].utilization * 100,
            )["mean"],
            "queueing": {
                "lambdaPerMinute": queueing["arrival_rate"],
                "muPerMinute": queueing["service_rate"],
                "rho": queueing["utilization"],
                "stable": queueing["stable"],
                "wqMinutes": queueing["queue_time"],
                "wMinutes": queueing["system_time"],
                "lq": queueing["queue_size"],
                "l": queueing["system_size"],
            },
        })
    return summaries


def build_dashboard_data(
    results: Dict[Scenario, List[ReplicationResult]],
    replicates: int,
    seed: int,
) -> dict:
    three_cashier_scenarios = [
        scenario for scenario in SCENARIOS if scenario.cashier_count == 3
    ]
    four_cashier_scenarios = [
        scenario for scenario in SCENARIOS if scenario.cashier_count == 4
    ]
    best_three = min(
        three_cashier_scenarios,
        key=lambda scenario: _metric_summary(
            results[scenario],
            lambda result: result.overall.average_wait_minutes,
        )["mean"],
    )
    best_four = min(
        four_cashier_scenarios,
        key=lambda scenario: _metric_summary(
            results[scenario],
            lambda result: result.overall.average_wait_minutes,
        )["mean"],
    )
    best_exclusive_three = min(
        (
            scenario for scenario in three_cashier_scenarios
            if not scenario.is_mixed
        ),
        key=lambda scenario: _metric_summary(
            results[scenario],
            lambda result: result.overall.average_wait_minutes,
        )["mean"],
    )

    scenario_rows = []
    for scenario, replications in results.items():
        overall = combine_metrics(result.overall for result in replications)
        scenario_rows.append({
            "name": scenario.label,
            "cashierCount": scenario.cashier_count,
            "wait": _metric_summary(
                replications,
                lambda result: result.overall.average_wait_minutes,
            ),
            "service": _metric_summary(
                replications,
                lambda result: result.overall.average_service_minutes,
            ),
            "usersPerDay": overall.customers / replicates,
        })

    category_rows = []
    first_scenario = next(iter(results))
    for action in ACTION_PROBABILITIES:
        for category in CATEGORY_NAMES:
            type_key = (action, category)
            first_scenario_replicas = results[first_scenario]
            metrics = [
                result.by_type[type_key]
                for result in first_scenario_replicas
            ]
            counts = [metric.customers for metric in metrics]
            min_count = min(counts)
            min_replications = [
                result.replication
                for result, count in zip(first_scenario_replicas, counts)
                if count == min_count
            ]
            minimum_scenarios = [
                scenario.label
                for scenario, replications in results.items()
                if any(
                    result.replication in min_replications
                    and result.by_type[type_key].customers == min_count
                    for result in replications
                )
            ]
            category_rows.append({
                "action": action,
                "category": category,
                "probability": ACTION_PROBABILITIES[action]
                * CATEGORY_PROBABILITIES[action][category],
                "averageUsers": sum(counts) / replicates,
                "minimumUsers": min_count,
                "minimumReplications": min_replications,
                "minimumScenarios": minimum_scenarios,
                "waitThree": _metric_summary(
                    results[best_three],
                    lambda result, key=type_key: result.by_type[
                        key
                    ].average_wait_minutes,
                ),
                "waitFour": _metric_summary(
                    results[best_four],
                    lambda result, key=type_key: result.by_type[
                        key
                    ].average_wait_minutes,
                ),
            })

    three_replications = results[best_three]
    four_replications = results[best_four]
    overall_difference = paired_wait_difference_stats(
        three_replications,
        four_replications,
        lambda result: result.overall.average_wait_minutes,
    )
    type_differences = []
    for row in category_rows:
        type_key = (row["action"], row["category"])
        difference = paired_wait_difference_stats(
            three_replications,
            four_replications,
            lambda result, key=type_key: result.by_type[
                key
            ].average_wait_minutes,
        )
        row["pairedWaitReduction"] = {
            "mean": difference[0],
            "sd": difference[1],
            "low": difference[2],
            "high": difference[3],
        }
        type_differences.append(difference)

    no_supported_worsening = all(difference[3] >= 0 for difference in type_differences)
    recommend_four = overall_difference[2] > 0 and no_supported_worsening

    mixed = next(scenario for scenario in SCENARIOS if scenario.is_mixed)
    exclusive_summaries = {
        "3 mixed": _cashier_summaries(mixed, results[mixed]),
        "best exclusive 3": _cashier_summaries(
            best_exclusive_three,
            results[best_exclusive_three],
        ),
    }

    return {
        "replicates": replicates,
        "seed": seed,
        "scenarioSummaries": scenario_rows,
        "bestThree": {
            "name": best_three.label,
            "wait": _metric_summary(
                results[best_three],
                lambda result: result.overall.average_wait_minutes,
            ),
        },
        "bestExclusiveThree": {
            "name": best_exclusive_three.label,
            "wait": _metric_summary(
                results[best_exclusive_three],
                lambda result: result.overall.average_wait_minutes,
            ),
        },
        "bestFour": {
            "name": best_four.label,
            "wait": _metric_summary(
                results[best_four],
                lambda result: result.overall.average_wait_minutes,
            ),
        },
        "fourthCashier": {
            "reduction": {
                "mean": overall_difference[0],
                "sd": overall_difference[1],
                "low": overall_difference[2],
                "high": overall_difference[3],
            },
            "recommend": recommend_four,
        },
        "categories": category_rows,
        "cashiers": exclusive_summaries,
        "replicationCounts": [
            {
                "replication": result.replication,
                "counts": [
                    {
                        "action": action,
                        "category": category,
                        "users": result.by_type[(action, category)].customers,
                    }
                    for action in ACTION_PROBABILITIES
                    for category in CATEGORY_NAMES
                ],
            }
            for result in results[first_scenario]
        ],
    }


def run_and_save(replicates: int, seed: int) -> dict:
    results = run_experiment(replicates, seed)
    prefix = str(OUTPUT_PREFIX)
    write_summary_csv(f"{prefix}_resumen.csv", results)
    write_replications_csv(f"{prefix}_replicas.csv", results)

    data = build_dashboard_data(results, replicates, seed)
    three_cashier_scenarios = [
        scenario for scenario in SCENARIOS if scenario.cashier_count == 3
    ]
    four_cashier_scenarios = [
        scenario for scenario in SCENARIOS if scenario.cashier_count == 4
    ]
    best_three = next(
        scenario for scenario in three_cashier_scenarios
        if scenario.label == data["bestThree"]["name"]
    )
    best_four = next(
        scenario for scenario in four_cashier_scenarios
        if scenario.label == data["bestFour"]["name"]
    )
    write_charts(prefix, results, best_three, best_four)
    return data


class BankRequestHandler(BaseHTTPRequestHandler):
    server_version = "BankSimulation/1.0"

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(
                200,
                HTML_PATH.read_bytes(),
                "text/html; charset=utf-8",
            )
            return

        if path.startswith("/files/"):
            file_name = path[len("/files/"):]
            if file_name not in DOWNLOADS:
                self._send_json(404, {"error": "Archivo no disponible."})
                return
            file_path = ROOT / file_name
            if not file_path.is_file():
                self._send_json(
                    404,
                    {"error": "Ejecute una simulación para generar este archivo."},
                )
                return
            content_type = (
                "image/svg+xml"
                if file_path.suffix == ".svg"
                else "text/csv; charset=utf-8"
            )
            self._send(200, file_path.read_bytes(), content_type)
            return

        self._send_json(404, {"error": "Ruta no encontrada."})

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/simulate":
            self._send_json(404, {"error": "Ruta no encontrada."})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "Longitud de solicitud inválida."})
            return
        if content_length < 1 or content_length > 10_000:
            self._send_json(413, {"error": "Tamaño de solicitud no permitido."})
            return

        try:
            request_data = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "El cuerpo debe ser JSON válido."})
            return
        if not isinstance(request_data, dict):
            self._send_json(400, {"error": "Se esperaba un objeto JSON."})
            return

        replicates = request_data.get("replicates", 10)
        seed = request_data.get("seed", 2026)
        if (
            not isinstance(replicates, int)
            or isinstance(replicates, bool)
            or not 10 <= replicates <= MAX_REPLICATES
        ):
            self._send_json(
                400,
                {"error": f"Las réplicas deben estar entre 10 y {MAX_REPLICATES}."},
            )
            return
        if not isinstance(seed, int) or isinstance(seed, bool):
            self._send_json(400, {"error": "La semilla debe ser un número entero."})
            return

        try:
            with SIMULATION_LOCK:
                data = run_and_save(replicates, seed)
        except Exception:
            self.log_error(
                "Error al ejecutar la simulación:\n%s",
                traceback.format_exc(),
            )
            self._send_json(
                500,
                {
                    "error": (
                        "No se pudo completar la simulación. "
                        "Revise la consola donde inició app_bancaria.py "
                        "para ver el detalle."
                    )
                },
            )
            return
        self._send_json(200, data)

    def log_message(self, format_string: str, *args) -> None:
        print(f"[web] {self.address_string()} - {format_string % args}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Interfaz web local para la simulación bancaria."
    )
    parser.add_argument("--puerto", type=int, default=8765)
    parser.add_argument(
        "--sin-abrir",
        action="store_true",
        help="No abrir automáticamente el navegador.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not 1 <= args.puerto <= 65535:
        raise SystemExit("El puerto debe estar entre 1 y 65535.")

    server = ThreadingHTTPServer(("127.0.0.1", args.puerto), BankRequestHandler)
    address = f"http://127.0.0.1:{args.puerto}"
    print(f"Interfaz bancaria disponible en {address}")
    print("Presione Ctrl+C en esta ventana para detener el servidor.")
    if not args.sin_abrir:
        threading.Timer(0.8, webbrowser.open_new_tab, args=(address,)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nCerrando la interfaz bancaria.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
