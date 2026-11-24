# SOLUCIÓN DE UN PROBLEMA BANCARIO

Aplicación local con interfaz web y motor de simulación en Python. La página
presenta el contexto, los datos de las tablas, los controles de ejecución,
comparaciones, estadísticas, tablas por réplica y gráficas.

## Iniciar la interfaz

1. Abre PowerShell en esta carpeta.
2. Ejecuta:

   ```powershell
   python .\app_bancaria.py
   ```

3. La aplicación abre el navegador en `http://127.0.0.1:8765`.
4. Elige al menos 10 réplicas y una semilla, y selecciona **Ejecutar
   comparación**.
5. Para cerrar el servidor local, vuelve a PowerShell y presiona `Ctrl+C`.

No requiere instalar paquetes. Usa únicamente la biblioteca estándar de
Python y escucha solo en el equipo local.

## Entregables

- [Interfaz web](./interfaz_bancaria.html)
- [Servidor local y API](./app_bancaria.py)
- [Motor de simulación](./simulacion_bancaria.py)

Al ejecutar una comparación, la aplicación genera los CSV y las gráficas SVG
que ofrece para descargar.

La aplicación compara tres cajas mixtas, las dos distribuciones exclusivas de
tres cajeros y tres alternativas con cuatro cajeros. Reporta promedios,
desviaciones estándar e intervalos de confianza del 95 %.
También estima λ, μ y ρ por cajero, comprueba la estabilidad M/M/1 y muestra
Wq y Lq teóricos como referencia de tasa promedio.

## Ejecución por terminal

El motor también se puede ejecutar sin interfaz:

```powershell
python .\simulacion_bancaria.py --replicaciones 10 --semilla 2026
```

Opciones disponibles:

```powershell
python .\simulacion_bancaria.py --replicaciones 100 --semilla 2026 --prefijo resultados_simulacion
```

## Interpretación del modelo

Las llegadas y los tiempos de servicio se generan con distribuciones
exponenciales condicionadas a la acción y categoría de usuario, usando las
medias de la actividad. Cada cajero mantiene una cola independiente. Dado que
las medias cambian entre categorías, el flujo combinado es una mezcla y no un
M/M/1 homogéneo con una sola tasa común; el programa conserva los parámetros
específicos provistos en el enunciado.

Las llegadas, categorías y tiempos de servicio de cada réplica se reutilizan
para comparar las configuraciones en condiciones equivalentes. La decisión
sobre un cuarto cajero se basa en diferencias emparejadas de espera y sus
intervalos de confianza.
