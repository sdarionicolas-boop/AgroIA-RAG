# Despliegue híbrido y compliance — copy para deck B2B

> Tres bloques de copy técnico-comercial para integrar en slides destinadas
> a equipos de compliance, IT y procurement de aseguradoras agrícolas.
> Cada bloque está calibrado en 80–120 palabras para una sola slide.
> Tono: ingeniero hablando a equipo de compliance. Sin lenguaje publicitario.

**Anclaje arquitectónico:** el stack descrito en [`AGENTS.md`](../../AGENTS.md) §1 (CDSE/EODAG, NASA POWER, SAM, PostgreSQL + pgvector, Ollama gemma3:4b) es naturalmente compatible con los tres modelos de despliegue; este documento no propone arquitectura nueva, traduce la existente a lenguaje de compliance.

---

## Bloque A — Segregación de datos

El perímetro del cliente expone únicamente datos geográficos no personales hacia la infraestructura de Copernicus: el polígono del lote en WKT (EPSG:4326), el año y mes consultado, y el token OAuth2 emitido por CDSE. No salen del perímetro identificadores de póliza, datos personales del asegurado (nombre, CUIT, dirección), historial de siniestros ni montos asegurados o liquidados. La cartera de pólizas, el historial de siniestros, los embeddings del módulo RAG (pgvector) y el LLM local (Ollama, gemma3:4b) residen exclusivamente en la base PostgreSQL on-premise del cliente. La asociación lote_id ↔ póliza vive en una tabla de mapeo administrada por el cliente, nunca expuesta al pipeline satelital.

_(120 palabras)_

---

## Bloque B — Modelos de despliegue

Tres configuraciones soportadas. **SaaS multi-tenant cifrado:** AgroIA hostea el stack; los tenants se separan por row-level security en PostgreSQL y cifrado at-rest; el extractor CDSE corre con credenciales del cliente delegadas; el LLM Ollama es compartido. **Single-tenant managed en VPC del cliente (AWS, Azure o GCP):** AgroIA opera el stack dentro de la VPC del cliente, con PostgreSQL, pgvector y Ollama dedicados; CDSE corre con credenciales del cliente; el acceso del equipo AgroIA es vía bastion auditado. **On-premise con licencia:** el cliente instala en su data center; AgroIA provee imágenes Docker firmadas y soporte L2; el cliente administra credenciales CDSE, certificados TLS y respaldos. La base de pólizas vive en la infraestructura del cliente en las tres opciones.

_(120 palabras)_

---

## Bloque C — Soberanía y trazabilidad

Cada consulta a la Statistical API de Copernicus se registra en una bitácora local con timestamp UTC, hash de la geometría, año y mes consultado, latencia y código HTTP. Los logs son append-only y la retención es configurable (default 90 días, extensible por configuración). El esquema cumple los principios de minimización de datos de la Ley 25.326 de Argentina: el path satelital no almacena PII del asegurado, por lo que la base operacional Copernicus queda fuera del alcance del régimen de datos personales. Para clientes con casa matriz en la Unión Europea, el cifrado in-transit (TLS 1.2+) y at-rest (AES-256), junto al flujo de borrado trazable sobre la tabla de pólizas, soportan el cumplimiento de los Art. 5 y 17 del RGPD.

_(120 palabras)_

---

## Tabla resumen por modelo

Para uso interno del equipo comercial — no para slide.

| Componente | SaaS multi-tenant | Single-tenant VPC | On-premise |
|---|---|---|---|
| Extractor CDSE | AgroIA cloud, credenciales cliente delegadas | VPC cliente, credenciales cliente | Data center cliente, credenciales cliente |
| LLM Ollama (gemma3:4b) | Compartido (multi-tenant) | Dedicado por cliente | Dedicado, cliente administra |
| PostgreSQL + pgvector (pólizas + embeddings) | Tenant aislado por RLS, cifrado at-rest | Dedicado en VPC cliente | Data center cliente |
| Credenciales CDSE | Vault AgroIA, scoped por tenant | Vault cliente | Vault cliente |
| Logs auditables | AgroIA + export al cliente | VPC cliente | Cliente |
| Quién administra TLS / certificados | AgroIA | Compartido (responsabilidad por capa) | Cliente |
| Quién administra respaldos | AgroIA | AgroIA dentro de VPC cliente | Cliente |
| Acceso del equipo AgroIA al stack | Tooling interno auditado | Bastion host auditado, ventanas declaradas | Solo a pedido del cliente |
| Tiempo típico de onboarding | 1–2 semanas | 4–6 semanas | 8–12 semanas |
| Encaja con | Aseguradoras con apetito cloud, sin política estricta de residencia | Aseguradoras con política de residencia clara y equipo cloud propio | Casas matrices con prohibición de cloud público / requisitos regulatorios fuertes |

---

## Notas para el equipo comercial

- Los tres bloques son intercambiables en slides; el orden recomendado para una primera reunión técnica con compliance es **A → C → B**: primero qué dato sale, después qué controles existen, después dónde corre cada pieza.
- Si la aseguradora pregunta por **certificaciones** (SOC 2, ISO 27001), responder con honestidad: el stack es auditable contra esos marcos pero la certificación formal es un compromiso a planificar, no una promesa actual.
- Si preguntan por **disponibilidad / SLA**, decir que el SLA depende del modelo (SaaS multi-tenant tiene SLA AgroIA; on-prem depende del cliente; single-tenant VPC es responsabilidad compartida). Cifras concretas se acuerdan en contrato.
- Si la conversación deriva a **uso del LLM con datos del asegurado**, aclarar: el LLM Ollama es local, el modelo no envía prompts a servidores externos, y el contexto que recibe el LLM proviene exclusivamente de la base de pólizas e informes on-prem.

---

_Documento mantenido por: equipo AgroIA. Última revisión: 2026-06-08._
