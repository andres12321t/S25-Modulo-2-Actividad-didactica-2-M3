import argparse
import csv
import math
import random
from dataclasses import dataclass, field
from html import escape
from statistics import mean
from typing import Dict, Iterable, List, Tuple


HOURS = 8
SIMULATION_SECONDS = HOURS * 60 * 60
CATEGORY_NAMES = ("Rapido", "Normal", "Lento", "Muy lento")
TypeKey = Tuple[str, str]

ACTION_PROBABILITIES = {
    "Retiro": 0.70,
    "Consignacion o pago": 0.30,
}

CATEGORY_PROBABILITIES = {
    "Retiro": {
        "Rapido": 0.23,
        "Normal": 0.40,
        "Lento": 0.17,
        "Muy lento": 0.20,
    },
    "Consignacion o pago": {
        "Rapido": 0.10,
        "Normal": 0.20,
        "Lento": 0.30,
        "Muy lento": 0.40,
    },
}

SERVICE_MEANS = {
    ("Retiro", "Rapido"): 1.0,
    ("Retiro", "Normal"): 2.0,
    ("Retiro", "Lento"): 3.0,
    ("Retiro", "Muy lento"): 4.0,
    ("Consignacion o pago", "Rapido"): 3.0,
    ("Consignacion o pago", "Normal"): 3.0,
    ("Consignacion o pago", "Lento"): 5.0,
    ("Consignacion o pago", "Muy lento"): 7.0,
}

ARRIVAL_MEANS = {
    ("Retiro", "Rapido"): 1.0,
    ("Retiro", "Normal"): 2.0,
    ("Retiro", "Lento"): 3.0,
    ("Retiro", "Muy lento"): 3.0,
    ("Consignacion o pago", "Rapido"): 1.0,
    ("Consignacion o pago", "Normal"): 2.0,
    ("Consignacion o pago", "Lento"): 3.0,
    ("Consignacion o pago", "Muy lento"): 4.0,
}

@dataclass(frozen=True)
class Scenario:
    label: str
    withdrawal_cashiers: int = 0
    payment_cashiers: int = 0
    mixed_cashiers: int = 0

    @property
    def cashier_count(self) -> int:
        return (
            self.withdrawal_cashiers
            + self.payment_cashiers
            + self.mixed_cashiers
        )

    @property
    def is_mixed(self) -> bool:
        return self.mixed_cashiers > 0


SCENARIOS = (
    Scenario("3 mixtos", mixed_cashiers=3),
    Scenario("1 retiro / 2 pagos", withdrawal_cashiers=1, payment_cashiers=2),
    Scenario("2 retiros / 1 pago", withdrawal_cashiers=2, payment_cashiers=1),
    Scenario("1 retiro / 3 pagos", withdrawal_cashiers=1, payment_cashiers=3),
    Scenario("2 retiros / 2 pagos", withdrawal_cashiers=2, payment_cashiers=2),
    Scenario("3 retiros / 1 pago", withdrawal_cashiers=3, payment_cashiers=1),
)

T_95_CRITICAL = {
    9: 2.262,
    10: 2.228,
    11: 2.201,
    12: 2.179,
    13: 2.160,
    14: 2.145,
    15: 2.131,
    16: 2.120,
    17: 2.110,
    18: 2.101,
    19: 2.093,
    20: 2.086,
    21: 2.080,
    22: 2.074,
    23: 2.069,
    24: 2.064,
    25: 2.060,
    26: 2.056,
    27: 2.052,
    28: 2.048,
    29: 2.045,
    30: 2.042,
}


@dataclass(frozen=True)
class Customer:
    arrival_seconds: float
    action: str
    category: str
    service_seconds: float
    route_draw: float


@dataclass
class Metrics:
    customers: int = 0
    wait_seconds: float = 0.0
    service_seconds: float = 0.0
    system_seconds: float = 0.0
    busy_seconds: float = 0.0

    def add(
        self,
        arrival_seconds: float,
        start_seconds: float,
        finish_seconds: float,
        service_seconds: float,
    ) -> None:
        self.customers += 1
        self.wait_seconds += start_seconds - arrival_seconds
        self.service_seconds += service_seconds
        self.system_seconds += finish_seconds - arrival_seconds
        self.busy_seconds += max(
            0.0,
            min(finish_seconds, SIMULATION_SECONDS)
            - max(start_seconds, 0.0),
        )

    @property
    def average_wait_minutes(self) -> float:
        return self.wait_seconds / self.customers / 60.0 if self.customers else 0.0

    @property
    def average_service_minutes(self) -> float:
        return (
            self.service_seconds / self.customers / 60.0
            if self.customers
            else 0.0
        )

    @property
    def utilization(self) -> float:
        return self.busy_seconds / SIMULATION_SECONDS


@dataclass
class ReplicationResult:
    scenario: Scenario
    replication: int
    overall: Metrics
    by_action: Dict[str, Metrics]
    by_type: Dict[TypeKey, Metrics]
    cashiers: List[Metrics] = field(default_factory=list)


def weighted_choice(rng: random.Random, probabilities: Dict[str, float]) -> str:
    return rng.choices(
        list(probabilities.keys()),
        weights=list(probabilities.values()),
        k=1,
    )[0]


def generate_customers(rng: random.Random) -> List[Customer]:
    """Genera las llegadas y servicios de una jornada de ocho horas."""
    customers = []
    arrival_seconds = 0.0

    while True:
        action = weighted_choice(rng, ACTION_PROBABILITIES)
        category = weighted_choice(rng, CATEGORY_PROBABILITIES[action])
        type_key = (action, category)

        mean_arrival_seconds = ARRIVAL_MEANS[type_key] * 60.0
        arrival_seconds += rng.expovariate(1.0 / mean_arrival_seconds)
        if arrival_seconds > SIMULATION_SECONDS:
            break

        mean_service_seconds = SERVICE_MEANS[type_key] * 60.0
        customers.append(
            Customer(
                arrival_seconds=arrival_seconds,
                action=action,
                category=category,
                service_seconds=rng.expovariate(1.0 / mean_service_seconds),
                route_draw=rng.random(),
            )
        )

    return customers


def run_scenario(
    customers: Iterable[Customer],
    scenario: Scenario,
    replication: int,
) -> ReplicationResult:
    """Ejecuta una réplica con colas independientes por cajero."""
    if scenario.cashier_count < 1:
        raise ValueError("El escenario debe tener al menos un cajero.")
    if not scenario.is_mixed and (
        scenario.withdrawal_cashiers < 1 or scenario.payment_cashiers < 1
    ):
        raise ValueError("Debe existir al menos un cajero exclusivo por acción.")

    cashier_available = [0.0] * scenario.cashier_count
    cashier_metrics = [Metrics() for _ in range(scenario.cashier_count)]
    overall = Metrics()
    by_action = {action: Metrics() for action in ACTION_PROBABILITIES}
    by_type = {
        (action, category): Metrics()
        for action in ACTION_PROBABILITIES
        for category in CATEGORY_NAMES
    }

    for customer in customers:
        if scenario.is_mixed:
            eligible = list(range(scenario.mixed_cashiers))
        elif customer.action == "Retiro":
            eligible = list(range(scenario.withdrawal_cashiers))
        else:
            eligible = list(
                range(
                    scenario.withdrawal_cashiers,
                    scenario.cashier_count,
                )
            )

        route_index = min(
            int(customer.route_draw * len(eligible)),
            len(eligible) - 1,
        )
        cashier_index = eligible[route_index]
        start_seconds = max(
            customer.arrival_seconds,
            cashier_available[cashier_index],
        )
        finish_seconds = start_seconds + customer.service_seconds
        cashier_available[cashier_index] = finish_seconds

        event_metrics = (
            customer.arrival_seconds,
            start_seconds,
            finish_seconds,
            customer.service_seconds,
        )
        cashier_metrics[cashier_index].add(*event_metrics)
        overall.add(*event_metrics)
        by_action[customer.action].add(*event_metrics)
        by_type[(customer.action, customer.category)].add(*event_metrics)

    return ReplicationResult(
        scenario=scenario,
        replication=replication,
        overall=overall,
        by_action=by_action,
        by_type=by_type,
        cashiers=cashier_metrics,
    )


def run_experiment(
    replicates: int,
    seed: int,
) -> Dict[Scenario, List[ReplicationResult]]:
    """Compara todos los escenarios con llegadas comunes por réplica."""
    results = {scenario: [] for scenario in SCENARIOS}
    for replication in range(1, replicates + 1):
        rng = random.Random(seed + replication * 1009)
        customers = generate_customers(rng)
        for scenario in SCENARIOS:
            results[scenario].append(
                run_scenario(customers, scenario, replication)
            )
    return results


def combine_metrics(metrics: Iterable[Metrics]) -> Metrics:
    combined = Metrics()
    for metric in metrics:
        combined.customers += metric.customers
        combined.wait_seconds += metric.wait_seconds
        combined.service_seconds += metric.service_seconds
        combined.system_seconds += metric.system_seconds
        combined.busy_seconds += metric.busy_seconds
    return combined


def estimate_mm1(metrics: Iterable[Metrics]) -> Dict[str, object]:
    """Estima parámetros M/M/1 por cajero a partir de las réplicas observadas."""
    metrics = list(metrics)
    combined = combine_metrics(metrics)
    operating_minutes = HOURS * 60.0 * len(metrics)
    service_minutes = combined.service_seconds / 60.0
    arrival_rate = combined.customers / operating_minutes if operating_minutes else 0.0
    service_rate = (
        combined.customers / service_minutes
        if service_minutes > 0.0
        else 0.0
    )
    utilization = arrival_rate / service_rate if service_rate else 0.0
    stable = service_rate > arrival_rate and service_rate > 0.0

    if stable:
        system_time = 1.0 / (service_rate - arrival_rate)
        queue_time = utilization / (service_rate - arrival_rate)
        system_size = arrival_rate * system_time
        queue_size = arrival_rate * queue_time
    else:
        system_time = None
        queue_time = None
        system_size = None
        queue_size = None

    return {
        "arrival_rate": arrival_rate,
        "service_rate": service_rate,
        "utilization": utilization,
        "stable": stable,
        "system_time": system_time,
        "queue_time": queue_time,
        "system_size": system_size,
        "queue_size": queue_size,
    }


def descriptive_stats(values: Iterable[float]) -> Tuple[float, float, float, float]:
    """Retorna media, desviación estándar e intervalo de confianza del 95 %."""
    values = list(values)
    if not values:
        raise ValueError("No hay datos para calcular estadísticas.")
    average = mean(values)
    if len(values) < 2:
        return average, 0.0, average, average

    variance = sum((value - average) ** 2 for value in values) / (len(values) - 1)
    standard_deviation = math.sqrt(variance)
    degrees_freedom = len(values) - 1
    critical_value = T_95_CRITICAL.get(degrees_freedom, 1.96)
    margin = critical_value * standard_deviation / math.sqrt(len(values))
    return average, standard_deviation, average - margin, average + margin


def metric_stat(
    replications: List[ReplicationResult],
    metric_getter,
) -> Tuple[float, float, float, float]:
    return descriptive_stats(metric_getter(result) for result in replications)


def wait_stats(replications: List[ReplicationResult]) -> Tuple[float, float, float, float]:
    return metric_stat(
        replications,
        lambda result: result.overall.average_wait_minutes,
    )


def type_wait_stats(
    replications: List[ReplicationResult],
    type_key: TypeKey,
) -> Tuple[float, float, float, float]:
    return metric_stat(
        replications,
        lambda result: result.by_type[type_key].average_wait_minutes,
    )


def paired_wait_difference_stats(
    three_cashier_results: List[ReplicationResult],
    four_cashier_results: List[ReplicationResult],
    metric_getter,
) -> Tuple[float, float, float, float]:
    """Compara réplicas emparejadas; valores positivos favorecen cuatro cajeros."""
    three_by_replication = {
        result.replication: result for result in three_cashier_results
    }
    four_by_replication = {
        result.replication: result for result in four_cashier_results
    }
    common_replicates = sorted(
        set(three_by_replication) & set(four_by_replication)
    )
    differences = [
        metric_getter(three_by_replication[replication])
        - metric_getter(four_by_replication[replication])
        for replication in common_replicates
    ]
    return descriptive_stats(differences)


def format_interval(lower: float, upper: float) -> str:
    return f"[{lower:.3f}, {upper:.3f}]"


def print_replication_counts(
    results: Dict[Scenario, List[ReplicationResult]],
) -> None:
    print("\nUSUARIOS POR TIPO EN CADA REPLICA")
    print("Conteo de llegadas; es común a todos los escenarios comparados.")
    type_keys = [
        (action, category)
        for action in ACTION_PROBABILITIES
        for category in CATEGORY_NAMES
    ]
    headers = [
        f"{action[:1]}-{category[:3]}"
        for action, category in type_keys
    ]
    print("Rep | " + " | ".join(f"{header:>7}" for header in headers))

    first_scenario = next(iter(results.values()))
    for result in first_scenario:
        counts = [result.by_type[key].customers for key in type_keys]
        print(
            f"{result.replication:>3} | "
            + " | ".join(f"{count:>7}" for count in counts)
        )


def print_scenario_summaries(
    results: Dict[Scenario, List[ReplicationResult]],
) -> None:
    print("\nCOMPARACION DE ESCENARIOS")
    print(
        f"{'Escenario':<25} {'Cajeros':>7} {'Espera min':>11} "
        f"{'Desv.Est.':>10} {'IC 95 %':>21} {'Atencion min':>13} "
        f"{'Usuarios/dia':>13}"
    )
    for scenario, replications in results.items():
        wait_mean, wait_sd, wait_low, wait_high = wait_stats(replications)
        overall = combine_metrics(result.overall for result in replications)
        average_service = descriptive_stats(
            result.overall.average_service_minutes
            for result in replications
        )[0]
        print(
            f"{scenario.label:<25} {scenario.cashier_count:>7} "
            f"{wait_mean:>11.3f} {wait_sd:>10.3f} "
            f"{format_interval(wait_low, wait_high):>21} "
            f"{average_service:>13.3f} "
            f"{overall.customers / len(replications):>13.2f}"
        )


def print_cashier_details(
    scenario: Scenario,
    replications: List[ReplicationResult],
) -> None:
    print(f"\nCAJEROS: {scenario.label}")
    if scenario.is_mixed:
        cashier_actions = ["Mixto"] * scenario.mixed_cashiers
    else:
        cashier_actions = (
            ["Retiro"] * scenario.withdrawal_cashiers
            + ["Consignacion o pago"] * scenario.payment_cashiers
        )

    cashier_results = []
    for index, action in enumerate(cashier_actions):
        cashier_metrics = [result.cashiers[index] for result in replications]
        combined = combine_metrics(cashier_metrics)
        wait_summary = descriptive_stats(
            metric.average_wait_minutes for metric in cashier_metrics
        )
        service_summary = descriptive_stats(
            metric.average_service_minutes for metric in cashier_metrics
        )
        utilization_summary = descriptive_stats(
            metric.utilization * 100 for metric in cashier_metrics
        )
        queueing = estimate_mm1(cashier_metrics)
        cashier_results.append((index, combined, service_summary))
        print(
            f"- Cajero {index + 1} ({action}): "
            f"{combined.customers / len(replications):.2f} usuarios/dia; "
            f"atencion {service_summary[0]:.3f} min; "
            f"espera {wait_summary[0]:.3f} min; "
            f"utilizacion simulada {utilization_summary[0]:.1f}%; "
            f"rho M/M/1 {queueing['utilization']:.3f}; "
            f"{'estable' if queueing['stable'] else 'inestable'}"
        )
        if queueing["stable"]:
            print(
                f"  Tasas estimadas: lambda={queueing['arrival_rate']:.4f}, "
                f"mu={queueing['service_rate']:.4f} usuarios/min; "
                f"Wq={queueing['queue_time']:.3f} min; "
                f"Lq={queueing['queue_size']:.3f} usuarios."
            )

    fastest = min(cashier_results, key=lambda row: row[2][0])
    slowest = max(cashier_results, key=lambda row: row[2][0])
    print(
        f"Menor atencion promedio: cajero {fastest[0] + 1} "
        f"({fastest[2][0]:.3f} min)."
    )
    print(
        f"Mayor atencion promedio: cajero {slowest[0] + 1} "
        f"({slowest[2][0]:.3f} min)."
    )


def print_wait_by_type(replications: List[ReplicationResult]) -> None:
    print("Espera promedio por tipo (media de réplicas, min):")
    for action in ACTION_PROBABILITIES:
        for category in CATEGORY_NAMES:
            key = (action, category)
            average, standard_deviation, lower, upper = type_wait_stats(
                replications,
                key,
            )
            print(
                f"- {action} / {category}: {average:.3f}; "
                f"DE {standard_deviation:.3f}; "
                f"IC 95 % {format_interval(lower, upper)}"
            )


def print_minimum_type_counts(
    results: Dict[Scenario, List[ReplicationResult]],
) -> None:
    print("\nMINIMO DE USUARIOS OBSERVADO POR TIPO")
    all_results = [
        (scenario, result)
        for scenario, replications in results.items()
        for result in replications
    ]
    for action in ACTION_PROBABILITIES:
        for category in CATEGORY_NAMES:
            key = (action, category)
            minimum = min(
                result.by_type[key].customers
                for _, result in all_results
            )
            replications = sorted({
                result.replication
                for _, result in all_results
                if result.by_type[key].customers == minimum
            })
            print(
                f"- {action} / {category}: {minimum}; "
                f"réplica(s) {', '.join(map(str, replications))}"
            )
    print(
        "Los conteos son iguales entre escenarios de una misma réplica porque "
        "la secuencia de llegadas se mantiene fija para una comparación justa."
    )


def print_fourth_cashier_decision(
    results: Dict[Scenario, List[ReplicationResult]],
) -> Tuple[Scenario, Scenario]:
    three_cashier = [scenario for scenario in SCENARIOS if scenario.cashier_count == 3]
    four_cashier = [scenario for scenario in SCENARIOS if scenario.cashier_count == 4]
    best_three = min(three_cashier, key=lambda scenario: wait_stats(results[scenario])[0])
    best_four = min(four_cashier, key=lambda scenario: wait_stats(results[scenario])[0])
    three_mean, _, three_low, three_high = wait_stats(results[best_three])
    four_mean, _, four_low, four_high = wait_stats(results[best_four])
    overall_difference = paired_wait_difference_stats(
        results[best_three],
        results[best_four],
        lambda result: result.overall.average_wait_minutes,
    )
    reduction = overall_difference[0]

    print("\nDECISION SOBRE EL CUARTO CAJERO")
    print(
        f"Mejor escenario de 3 cajeros: {best_three.label}; "
        f"espera {three_mean:.3f} min, IC 95 % "
        f"{format_interval(three_low, three_high)}."
    )
    print(
        f"Mejor escenario de 4 cajeros: {best_four.label}; "
        f"espera {four_mean:.3f} min, IC 95 % "
        f"{format_interval(four_low, four_high)}."
    )
    print(
        f"Reduccion emparejada estimada: {reduction:.3f} min por usuario; "
        f"IC 95 % {format_interval(overall_difference[2], overall_difference[3])}."
    )
    print("Reduccion emparejada por tipo (3 cajeros - 4 cajeros):")
    statistically_worse_types = []
    for action in ACTION_PROBABILITIES:
        for category in CATEGORY_NAMES:
            key = (action, category)
            difference = paired_wait_difference_stats(
                results[best_three],
                results[best_four],
                lambda result, type_key=key: result.by_type[
                    type_key
                ].average_wait_minutes,
            )
            print(
                f"- {action} / {category}: {difference[0]:.3f} min; "
                f"IC 95 % {format_interval(difference[2], difference[3])}"
            )
            if difference[3] < 0:
                statistically_worse_types.append((action, category))

    if overall_difference[2] > 0.0 and not statistically_worse_types:
        print(
            "Recomendación: las réplicas emparejadas muestran una reducción "
            "global de espera; ninguna categoría presenta un empeoramiento "
            "estadísticamente respaldado al 95 %."
        )
    else:
        print(
            "Recomendación: con este número de réplicas no se confirma una "
            "mejora global estadísticamente concluyente sin deterioro por "
            "categoría; ampliar las réplicas y considerar los costos."
        )

    return best_three, best_four


def write_summary_csv(
    path: str,
    results: Dict[Scenario, List[ReplicationResult]],
) -> None:
    headers = [
        "escenario",
        "cajeros",
        "replicas",
        "registro",
        "accion",
        "tipo_usuario",
        "usuarios_promedio_dia",
        "espera_promedio_min",
        "desv_est_espera_min",
        "ic95_espera_inferior_min",
        "ic95_espera_superior_min",
        "atencion_promedio_min",
        "desv_est_atencion_min",
        "ic95_atencion_inferior_min",
        "ic95_atencion_superior_min",
        "utilizacion_promedio_pct",
        "lambda_usuarios_min",
        "mu_usuarios_min",
        "rho_mm1",
        "estable_mm1",
        "wq_mm1_min",
        "w_mm1_min",
        "lq_mm1_usuarios",
        "l_mm1_usuarios",
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as output:
        writer = csv.writer(output)
        writer.writerow(headers)
        for scenario, replications in results.items():
            for action in ACTION_PROBABILITIES:
                action_metrics = [result.by_action[action] for result in replications]
                wait = descriptive_stats(
                    metric.average_wait_minutes for metric in action_metrics
                )
                service = descriptive_stats(
                    metric.average_service_minutes for metric in action_metrics
                )
                writer.writerow([
                    scenario.label, scenario.cashier_count, len(replications),
                    "accion", action, "",
                    f"{mean(metric.customers for metric in action_metrics):.4f}",
                    f"{wait[0]:.6f}", f"{wait[1]:.6f}",
                    f"{wait[2]:.6f}", f"{wait[3]:.6f}",
                    f"{service[0]:.6f}", f"{service[1]:.6f}",
                    f"{service[2]:.6f}", f"{service[3]:.6f}", "",
                    "", "", "", "", "", "", "", "",
                ])

            for action in ACTION_PROBABILITIES:
                for category in CATEGORY_NAMES:
                    metrics = [
                        result.by_type[(action, category)]
                        for result in replications
                    ]
                    wait = descriptive_stats(
                        metric.average_wait_minutes for metric in metrics
                    )
                    service = descriptive_stats(
                        metric.average_service_minutes for metric in metrics
                    )
                    writer.writerow([
                        scenario.label, scenario.cashier_count, len(replications),
                        "tipo_usuario", action, category,
                        f"{mean(metric.customers for metric in metrics):.4f}",
                        f"{wait[0]:.6f}", f"{wait[1]:.6f}",
                        f"{wait[2]:.6f}", f"{wait[3]:.6f}",
                        f"{service[0]:.6f}", f"{service[1]:.6f}",
                        f"{service[2]:.6f}", f"{service[3]:.6f}", "",
                        "", "", "", "", "", "", "", "",
                    ])

            overall_wait = descriptive_stats(
                result.overall.average_wait_minutes
                for result in replications
            )
            overall_service = descriptive_stats(
                result.overall.average_service_minutes
                for result in replications
            )
            overall = combine_metrics(result.overall for result in replications)
            writer.writerow([
                scenario.label, scenario.cashier_count, len(replications),
                "total", "", "",
                f"{overall.customers / len(replications):.4f}",
                f"{overall_wait[0]:.6f}", f"{overall_wait[1]:.6f}",
                f"{overall_wait[2]:.6f}", f"{overall_wait[3]:.6f}",
                f"{overall_service[0]:.6f}", f"{overall_service[1]:.6f}",
                f"{overall_service[2]:.6f}", f"{overall_service[3]:.6f}", "",
                "", "", "", "", "", "", "", "",
            ])

            cashier_labels = (
                ["Mixto"] * scenario.mixed_cashiers
                if scenario.is_mixed
                else ["Retiro"] * scenario.withdrawal_cashiers
                + ["Consignacion o pago"] * scenario.payment_cashiers
            )
            for index, action in enumerate(cashier_labels):
                metrics = [result.cashiers[index] for result in replications]
                wait = descriptive_stats(
                    metric.average_wait_minutes for metric in metrics
                )
                service = descriptive_stats(
                    metric.average_service_minutes for metric in metrics
                )
                utilization = descriptive_stats(
                    metric.utilization * 100 for metric in metrics
                )
                queueing = estimate_mm1(metrics)
                writer.writerow([
                    scenario.label, scenario.cashier_count, len(replications),
                    "cajero", action, f"Cajero {index + 1}",
                    f"{mean(metric.customers for metric in metrics):.4f}",
                    f"{wait[0]:.6f}", f"{wait[1]:.6f}",
                    f"{wait[2]:.6f}", f"{wait[3]:.6f}",
                    f"{service[0]:.6f}", f"{service[1]:.6f}",
                    f"{service[2]:.6f}", f"{service[3]:.6f}",
                    f"{utilization[0]:.6f}",
                    f"{queueing['arrival_rate']:.8f}",
                    f"{queueing['service_rate']:.8f}",
                    f"{queueing['utilization']:.8f}",
                    "si" if queueing["stable"] else "no",
                    (
                        f"{queueing['queue_time']:.8f}"
                        if queueing["stable"]
                        else ""
                    ),
                    (
                        f"{queueing['system_time']:.8f}"
                        if queueing["stable"]
                        else ""
                    ),
                    (
                        f"{queueing['queue_size']:.8f}"
                        if queueing["stable"]
                        else ""
                    ),
                    (
                        f"{queueing['system_size']:.8f}"
                        if queueing["stable"]
                        else ""
                    ),
                ])


def write_replications_csv(
    path: str,
    results: Dict[Scenario, List[ReplicationResult]],
) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as output:
        writer = csv.writer(output)
        writer.writerow([
            "escenario",
            "replica",
            "registro",
            "cajero",
            "accion",
            "tipo_usuario",
            "usuarios",
            "espera_promedio_min",
            "atencion_promedio_min",
            "utilizacion_pct",
            "lambda_usuarios_min",
            "mu_usuarios_min",
            "rho_mm1",
            "estable_mm1",
            "wq_mm1_min",
            "w_mm1_min",
            "lq_mm1_usuarios",
            "l_mm1_usuarios",
        ])
        for scenario, replications in results.items():
            cashier_labels = (
                ["Mixto"] * scenario.mixed_cashiers
                if scenario.is_mixed
                else ["Retiro"] * scenario.withdrawal_cashiers
                + ["Consignacion o pago"] * scenario.payment_cashiers
            )
            for result in replications:
                for action in ACTION_PROBABILITIES:
                    for category in CATEGORY_NAMES:
                        metric = result.by_type[(action, category)]
                        writer.writerow([
                            scenario.label, result.replication, "tipo_usuario",
                            "", action, category, metric.customers,
                            f"{metric.average_wait_minutes:.6f}",
                            f"{metric.average_service_minutes:.6f}", "",
                            "", "", "", "", "", "", "", "",
                        ])
                for index, (action, metric) in enumerate(
                    zip(cashier_labels, result.cashiers),
                    start=1,
                ):
                    queueing = estimate_mm1([metric])
                    writer.writerow([
                        scenario.label, result.replication, "cajero", index,
                        action, "", metric.customers,
                        f"{metric.average_wait_minutes:.6f}",
                        f"{metric.average_service_minutes:.6f}",
                        f"{metric.utilization * 100:.4f}",
                        f"{queueing['arrival_rate']:.8f}",
                        f"{queueing['service_rate']:.8f}",
                        f"{queueing['utilization']:.8f}",
                        "si" if queueing["stable"] else "no",
                        (
                            f"{queueing['queue_time']:.8f}"
                            if queueing["stable"]
                            else ""
                        ),
                        (
                            f"{queueing['system_time']:.8f}"
                            if queueing["stable"]
                            else ""
                        ),
                        (
                            f"{queueing['queue_size']:.8f}"
                            if queueing["stable"]
                            else ""
                        ),
                        (
                            f"{queueing['system_size']:.8f}"
                            if queueing["stable"]
                            else ""
                        ),
                    ])


def write_bar_chart(
    path: str,
    title: str,
    labels: List[str],
    values: List[float],
    errors: List[float],
    series_label: str,
) -> None:
    width = 1050
    left = 270
    right = 95
    top = 80
    row_height = 48
    height = top + row_height * len(labels) + 60
    plot_width = width - left - right
    max_value = max((value + error for value, error in zip(values, errors)), default=1.0)
    max_value = max(max_value * 1.12, 1.0)

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{width / 2}" y="35" text-anchor="middle" '
        f'font-family="Segoe UI,Arial" font-size="22" font-weight="bold" '
        f'fill="#183153">{escape(title)}</text>',
    ]

    for tick in range(6):
        value = max_value * tick / 5
        x = left + plot_width * tick / 5
        lines.append(
            f'<line x1="{x:.1f}" y1="{top - 8}" x2="{x:.1f}" '
            f'y2="{height - 45}" stroke="#d7dee8"/>'
        )
        lines.append(
            f'<text x="{x:.1f}" y="{height - 20}" text-anchor="middle" '
            f'font-family="Segoe UI,Arial" font-size="12" fill="#43536b">'
            f'{value:.1f}</text>'
        )

    for index, (label, value, error) in enumerate(zip(labels, values, errors)):
        center_y = top + index * row_height + row_height / 2
        bar_width = plot_width * value / max_value
        error_width = plot_width * error / max_value
        lines.extend([
            f'<text x="{left - 12}" y="{center_y + 5:.1f}" text-anchor="end" '
            f'font-family="Segoe UI,Arial" font-size="13" fill="#23344d">'
            f'{escape(label)}</text>',
            f'<rect x="{left}" y="{center_y - 12:.1f}" width="{bar_width:.1f}" '
            f'height="24" rx="4" fill="#2878b5"/>',
            f'<line x1="{left + bar_width - error_width:.1f}" '
            f'y1="{center_y:.1f}" x2="{left + bar_width + error_width:.1f}" '
            f'y2="{center_y:.1f}" stroke="#172b4d" stroke-width="2"/>',
            f'<line x1="{left + bar_width - error_width:.1f}" '
            f'y1="{center_y - 5:.1f}" x2="{left + bar_width - error_width:.1f}" '
            f'y2="{center_y + 5:.1f}" stroke="#172b4d" stroke-width="2"/>',
            f'<line x1="{left + bar_width + error_width:.1f}" '
            f'y1="{center_y - 5:.1f}" x2="{left + bar_width + error_width:.1f}" '
            f'y2="{center_y + 5:.1f}" stroke="#172b4d" stroke-width="2"/>',
            f'<text x="{left + bar_width + error_width + 7:.1f}" '
            f'y="{center_y + 5:.1f}" font-family="Segoe UI,Arial" '
            f'font-size="12" fill="#23344d">{value:.2f}</text>',
        ])

    lines.extend([
        f'<text x="{left + plot_width / 2:.1f}" y="{height - 2}" '
        f'text-anchor="middle" font-family="Segoe UI,Arial" font-size="12" '
        f'fill="#43536b">{escape(series_label)}; barras de error: IC 95 %</text>',
        "</svg>",
    ])
    with open(path, "w", encoding="utf-8") as output:
        output.write("\n".join(lines))


def write_grouped_chart(
    path: str,
    title: str,
    labels: List[str],
    first_label: str,
    first_values: List[float],
    second_label: str,
    second_values: List[float],
) -> None:
    width = 1150
    left = 330
    right = 85
    top = 105
    row_height = 58
    height = top + row_height * len(labels) + 70
    plot_width = width - left - right
    max_value = max(first_values + second_values + [1.0]) * 1.15
    colors = ("#2878b5", "#e58b2a")
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{width / 2}" y="35" text-anchor="middle" '
        f'font-family="Segoe UI,Arial" font-size="21" font-weight="bold" '
        f'fill="#183153">{escape(title)}</text>',
    ]

    for legend_index, label in enumerate((first_label, second_label)):
        x = left + legend_index * 230
        lines.extend([
            f'<rect x="{x}" y="55" width="16" height="16" fill="{colors[legend_index]}"/>',
            f'<text x="{x + 23}" y="68" font-family="Segoe UI,Arial" '
            f'font-size="13" fill="#23344d">{escape(label)}</text>',
        ])

    for index, label in enumerate(labels):
        center_y = top + index * row_height
        lines.append(
            f'<text x="{left - 12}" y="{center_y + 6}" text-anchor="end" '
            f'font-family="Segoe UI,Arial" font-size="12" fill="#23344d">'
            f'{escape(label)}</text>'
        )
        for offset, value, color in (
            (-9, first_values[index], colors[0]),
            (9, second_values[index], colors[1]),
        ):
            bar_width = plot_width * value / max_value
            lines.extend([
                f'<rect x="{left}" y="{center_y + offset - 6}" '
                f'width="{bar_width:.1f}" height="12" rx="3" fill="{color}"/>',
                f'<text x="{left + bar_width + 6:.1f}" '
                f'y="{center_y + offset + 4}" font-family="Segoe UI,Arial" '
                f'font-size="11" fill="#23344d">{value:.2f}</text>',
            ])

    lines.extend([
        f'<text x="{left + plot_width / 2:.1f}" y="{height - 10}" '
        f'text-anchor="middle" font-family="Segoe UI,Arial" font-size="12" '
        f'fill="#43536b">Tiempo promedio de espera (min)</text>',
        "</svg>",
    ])
    with open(path, "w", encoding="utf-8") as output:
        output.write("\n".join(lines))


def write_charts(
    prefix: str,
    results: Dict[Scenario, List[ReplicationResult]],
    best_three: Scenario,
    best_four: Scenario,
) -> None:
    scenario_values = [
        (scenario, wait_stats(replications))
        for scenario, replications in results.items()
    ]
    write_bar_chart(
        f"{prefix}_espera_escenarios.svg",
        "Espera promedio por configuración",
        [scenario.label for scenario, _ in scenario_values],
        [stats[0] for _, stats in scenario_values],
        [(stats[3] - stats[2]) / 2 for _, stats in scenario_values],
        "Espera promedio (min)",
    )

    type_keys = [
        (action, category)
        for action in ACTION_PROBABILITIES
        for category in CATEGORY_NAMES
    ]
    write_grouped_chart(
        f"{prefix}_espera_tipos.svg",
        "Espera por tipo: mejor escenario de 3 frente a 4 cajeros",
        [f"{action} - {category}" for action, category in type_keys],
        best_three.label,
        [type_wait_stats(results[best_three], key)[0] for key in type_keys],
        best_four.label,
        [type_wait_stats(results[best_four], key)[0] for key in type_keys],
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Simulación discreta de cajeros bancarios."
    )
    parser.add_argument(
        "--replicaciones",
        type=int,
        default=10,
        help="Jornadas simuladas, mínimo 10 (por defecto: 10).",
    )
    parser.add_argument(
        "--semilla",
        type=int,
        default=2026,
        help="Semilla para reproducir los resultados.",
    )
    parser.add_argument(
        "--prefijo",
        default="resultados_simulacion",
        help="Prefijo de los archivos CSV y gráficos de salida.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.replicaciones < 10:
        raise SystemExit("La actividad solicita al menos 10 replicaciones.")

    results = run_experiment(args.replicaciones, args.semilla)
    print(
        f"SIMULACION BANCARIA: {args.replicaciones} replicas de "
        f"{HOURS} horas; semilla {args.semilla}"
    )
    print_replication_counts(results)
    print_scenario_summaries(results)

    mixed_scenario = SCENARIOS[0]
    best_exclusive_three = min(
        (SCENARIOS[1], SCENARIOS[2]),
        key=lambda scenario: wait_stats(results[scenario])[0],
    )
    print_cashier_details(mixed_scenario, results[mixed_scenario])
    print(
        "\nMEJOR DISTRIBUCION EXCLUSIVA CON TRES CAJEROS: "
        f"{best_exclusive_three.label}"
    )
    print_cashier_details(
        best_exclusive_three,
        results[best_exclusive_three],
    )
    print("\nESPERAS POR TIPO EN LA MEJOR DISTRIBUCION EXCLUSIVA")
    print_wait_by_type(results[best_exclusive_three])
    print_minimum_type_counts(results)
    best_three, best_four = print_fourth_cashier_decision(results)

    prefix = args.prefijo
    write_summary_csv(f"{prefix}_resumen.csv", results)
    write_replications_csv(f"{prefix}_replicas.csv", results)
    write_charts(prefix, results, best_three, best_four)
    print(
        "\nArchivos generados: "
        f"{prefix}_resumen.csv, {prefix}_replicas.csv, "
        f"{prefix}_espera_escenarios.svg y {prefix}_espera_tipos.svg"
    )


if __name__ == "__main__":
    main()
