from pathlib import Path

from sift.app.runtime import runtime_capacity


ROOT = Path(__file__).resolve().parents[1]


def test_root_has_single_docker_and_dependency_files():
    assert sorted(path.name for path in ROOT.glob('docker-compose*.yml')) == ['docker-compose.yml']
    assert sorted(path.name for path in ROOT.glob('requirements*.txt')) == ['requirements.txt']
    assert (ROOT / '.env.docker').exists()
    assert not (ROOT / '.env.example').exists()
    assert not (ROOT / '.env.docker.example').exists()


def test_dockerfile_uses_single_requirements_file():
    dockerfile = (ROOT / 'Dockerfile').read_text(encoding='utf-8')
    assert 'requirements.txt' in dockerfile
    assert 'requirements.docker.txt' not in dockerfile
    assert 'sift.app.main:app' in dockerfile


def test_no_migration_phase_files_left_in_final_package():
    assert not list(ROOT.glob('MIGRATION_PHASE*.md'))


def test_runtime_capacity_reads_project_root_for_env_fallbacks():
    assert runtime_capacity.ROOT_DIR == ROOT
    assert (runtime_capacity.ROOT_DIR / 'Dockerfile').exists()
