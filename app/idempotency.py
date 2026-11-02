"""Idempotencia de POST /orders. Aquí vive la huella del contenido de una petición.

La clave la envía el cliente en el encabezado Idempotency-Key. La huella permite saber si una
segunda petición con la misma clave trae el mismo contenido (se repite el resultado original)
o uno distinto (conflicto).
"""
import hashlib
import json


def request_fingerprint(data):
    """SHA-256 del contenido ya validado. El mismo contenido siempre da la misma huella.

    Se calcula sobre OrderInput, así que los espacios sobrantes de los textos (que Pydantic recorta)
    y el orden de las claves del JSON no cambian la huella. El orden de las líneas sí la cambia.
    """
    canonical = json.dumps(data.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
