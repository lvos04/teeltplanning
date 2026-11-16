# Teeltplanning

Zelf-gehoste webapp die het Excel-werkboek *Teeltprogramma maken* vervangt: teeltplanning voor 40 kappen over
meerdere rondes, met voorstellen, handmatige overschrijving en doorrekening. Het leidende document is
[docs/BOUWPLAN.md](docs/BOUWPLAN.md). De bouw gaat per fase (§14); zie [docs/STATUS.md](docs/STATUS.md) zodra die
er is.

## Ontwikkelen

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/pytest -q
python -m tests.synthetisch proeven/uitvoer      # synthetische testwerkboeken
```

Documentatie: [STATUS](docs/STATUS.md) · [OPEN_VRAGEN](docs/OPEN_VRAGEN.md) · [ARCHITECTUUR](docs/ARCHITECTUUR.md) ·
[TRUENAS](docs/TRUENAS.md) · [EXCEL](docs/EXCEL.md) · [WIJZIGINGEN](docs/WIJZIGINGEN.md) ·
[koppeling-api](docs/koppeling-api.md)
