# GapForge: refactor incremental del laboratorio CKA

## Objetivo

Convertir el simulador CKA local en un laboratorio personal de ingeniería generalista
sin destruir ni debilitar el modo CKA. El terminal es el punto de entrada; la
orquestación, el aislamiento y la corrección pertenecen al backend.

## Estado de este PR

Este primer corte **solo extrae la presentación del terminal** a
`frontend/src/terminalPresentation.ts`. Banner, prompt, pistas visuales e
identificación accesible dejan de estar codificados dentro de xterm. El perfil
CKA mantiene literalmente el comportamiento y las etiquetas existentes.

**No hay todavía un ejecutor Linux genérico, shell/PTY, SSH al host, nuevas rutas
de laboratorio ni retos ajenos a Kubernetes.** No se anuncian como disponibles.
Tampoco cambia el contrato de comandos ni la política de seguridad CKA.

## Contrato previsto del siguiente corte

Cada tipo de laboratorio (CKA, Linux, redes, bases de datos, etc.) aporta:

- Metadatos versionados: identificador, dominio, objetivos, dificultad y TTL.
- Un adaptador de aprovisionamiento y limpieza de recursos **propios**, con límites
  explícitos de CPU/memoria/procesos/red y sin adoptar recursos externos.
- Un transporte de comandos acotado al sandbox. En CKA sigue siendo el ejecutor
  restringido actual; para Linux, un contenedor efímero independiente, no el host.
- Un corrector determinista de estado final e invariantes de seguridad.
- Evidencia de intentos, pistas, gastos de créditos y resultados, sin confiar la
  calificación a un LLM.

El diálogo con un peer engineer y la generación Ollama serán **opt-in** y no
podrán otorgar permisos al ejecutor. Los escenarios generados requieren schema,
revisión y smoke reproducible con solución de referencia.

## Invariantes

1. `make up` continúa siendo local-only y escucha en loopback.
2. El laboratorio nunca recibe el socket Docker del anfitrión ni credenciales
   cloud; tampoco utiliza `~/.kube/config`.
3. CKA conserva su aislamiento, expiración, limpieza, editor nano y correctores.
4. Ninguna elección de perfil visual autoriza comandos en el backend.
5. Cada ampliación se entrega con tests de integración de inicio, ejecución,
   evaluación, caducidad y cleanup; no se habilita un nuevo dominio antes.
