# Testing Standards

## Framework & Configuration

- pytest is the sole test runner — never unittest-style classes
- pytest-asyncio for async test support with `asyncio_mode = "auto"`
- pytest-cov for coverage tracking
- factory-boy for test data factories

## Coverage Requirements

- Minimum 80% branch coverage enforced in pyproject.toml (`fail_under = 80`)
- Coverage measured on `src/` only — not tests themselves
- Generate HTML reports locally: `uv run just test-cov`
- CI runs coverage on every PR and blocks merge if below threshold

## Test Organization

```
tests/
├── conftest.py         # Shared fixtures: async client, test db session, factories
├── unit/               # Pure logic tests — no I/O, no database
│   ├── test_schemas.py
│   └── test_services.py
└── integration/        # Tests that hit database or external services
    ├── test_api.py
    └── test_repositories.py
```

- Unit tests for schemas, validators, pure business logic, utility functions
- Integration tests for API endpoints, database operations, external service calls
- Name test files `test_<module>.py` — name test functions `test_<behavior>()`

## Async Testing Pattern

Use `httpx.AsyncClient` with FastAPI for integration tests:

```python
import pytest
from httpx import ASGITransport, AsyncClient
from myproject.main import app

@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

async def test_create_user(client: AsyncClient):
    response = await client.post("/users", json={"name": "Alice", "email": "alice@example.com"})
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Alice"
```

## Test Database

- Use a separate test database — never the development database
- Apply migrations in the test session fixture
- Wrap each test in a transaction and roll back after — tests must not leak state
- Use `async_sessionmaker` scoped to the test function

## Fixtures & Factories

- Use factory-boy for complex object creation — not raw fixture dicts
- Define factories in `tests/factories.py`
- Use `conftest.py` for shared fixtures; keep test-specific fixtures local
- Prefer parametrize over copy-pasting similar tests

```python
import factory
from myproject.models.user import User

class UserFactory(factory.Factory):
    class Meta:
        model = User

    name = factory.Faker("name")
    email = factory.LazyAttribute(lambda obj: f"{obj.name.lower().replace(' ', '.')}@example.com")
```

## What to Test

Every new piece of code must have corresponding tests:

- **API endpoints**: status codes, response shapes, error cases, auth
- **Pydantic schemas**: validation rules, edge cases, serialization from ORM
- **Service functions**: business logic paths, including error/edge cases
- **Database operations**: CRUD, constraints, relationships, migrations

## What NOT to Test

- Framework internals (FastAPI routing, SQLAlchemy SQL generation)
- Third-party library behavior
- Trivial getters/setters with no logic

## Test Quality Rules

- Tests must be deterministic — no random data without seeding, no time-dependent assertions
- Each test asserts one behavior — not multiple unrelated things
- Use descriptive names: `test_create_user_with_duplicate_email_returns_409` not `test_create_user_2`
- No `time.sleep` in tests — use async patterns or mock timers
- Assert on specific values, not just truthiness: `assert response.status_code == 201` not `assert response.ok`
