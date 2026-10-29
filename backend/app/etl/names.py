"""Deterministic synthetic first names (assumption A14: Olist contains no customer names)."""

import hashlib

FIRST_NAMES = [
    "Ana", "Bruno", "Camila", "Daniel", "Eduarda", "Felipe", "Gabriela", "Gustavo", "Helena", "Igor",
    "Juliana", "Lucas", "Mariana", "Mateus", "Natalia", "Otavio", "Paula", "Rafael", "Sofia", "Thiago",
    "Beatriz", "Caio", "Fernanda", "Diego", "Larissa", "Leonardo", "Isabela", "Rodrigo", "Vitoria", "Pedro",
    "Aline", "Andre", "Carolina", "Fabio", "Leticia", "Marcelo", "Patricia", "Renato", "Tatiana", "Vinicius",
    "Bianca", "Carlos", "Debora", "Eduardo", "Flavia", "Guilherme", "Julia", "Marcos", "Priscila", "Ricardo",
]


def synthetic_first_name(customer_unique_id: str) -> str:
    idx = int(hashlib.sha256(customer_unique_id.encode()).hexdigest()[:8], 16) % len(FIRST_NAMES)
    return FIRST_NAMES[idx]
