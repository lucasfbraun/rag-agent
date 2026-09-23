from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_alembic_tem_um_unico_head():
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["c6f2b8e91a34"]
