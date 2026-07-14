# Contributing to Sentinel OS

Welcome! 👋 We're thrilled you want to contribute to Sentinel OS. Whether you're fixing bugs, adding features, improving documentation, or suggesting improvements, your help makes this project better.

This guide will walk you through everything you need to know to get started.

---

## Code of Conduct

We are committed to providing a welcoming and inspiring community for all. Please read and follow our code of conduct:

- **Be respectful** - Treat all community members with kindness and respect
- **Be inclusive** - Welcome people of all backgrounds and experiences
- **Be constructive** - Provide helpful feedback and collaborate towards solutions
- **Report issues** - If you see harassment or inappropriate behavior, please reach out privately

---

## Getting Started

### 1. Fork the Repository

Go to https://github.com/wking53214/sentinel_os and click **"Fork"** in the top right corner. This creates your own copy of the project.

### 2. Clone Your Fork Locally

```bash
git clone https://github.com/YOUR-USERNAME/sentinel_os.git
cd sentinel_os
```

### 3. Add the Original Repository as Upstream

This lets you stay in sync with the main project:

```bash
git remote add upstream https://github.com/wking53214/sentinel_os.git
git remote -v  # Verify you have both 'origin' and 'upstream'
```

---

## Development Environment Setup

### Prerequisites

- Python 3.8 or higher
- pip (Python package manager)
- Git
- PostgreSQL 13+ (for full test suite)
- Docker and Docker Compose (optional, for full stack)

### Choose Your Platform

#### macOS

```bash
# Install Homebrew (if not already installed)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install Python and PostgreSQL
brew install python@3.11 postgresql

# Start PostgreSQL
brew services start postgresql

# Verify installation
python3 --version
psql --version
```

#### Linux (Ubuntu/Debian)

```bash
# Update package list
sudo apt-get update

# Install Python and PostgreSQL
sudo apt-get install -y python3.11 python3.11-venv python3-pip postgresql postgresql-contrib

# Start PostgreSQL
sudo systemctl start postgresql
sudo systemctl enable postgresql

# Verify installation
python3 --version
psql --version
```

#### Windows (WSL2 Recommended)

```bash
# Enable WSL2 and install Ubuntu
# See: https://docs.microsoft.com/en-us/windows/wsl/install

# Inside WSL2 Ubuntu terminal:
sudo apt-get update
sudo apt-get install -y python3.11 python3.11-venv python3-pip postgresql postgresql-contrib

# Start PostgreSQL
sudo service postgresql start

# Verify installation
python3 --version
psql --version
```

#### Chromebook (Linux Container)

```bash
# Open Linux terminal (Ctrl+Alt+T in terminal, or via settings)
sudo apt-get update
sudo apt-get install -y python3.11 python3.11-venv python3-pip postgresql postgresql-contrib

# Start PostgreSQL
sudo service postgresql start

# Verify installation
python3 --version
psql --version
```

### Set Up Virtual Environment

```bash
# Navigate to project directory
cd sentinel_os

# Create virtual environment
python3 -m venv venv

# Activate it
# On macOS/Linux/Chromebook:
source venv/bin/activate
# On Windows:
venv\Scripts\activate

# Upgrade pip
pip install --upgrade pip

# Install dependencies
pip install -r sentinel_os/requirements.txt

# Verify installation
python3 -c "import sys; print(f'Python {sys.version} in {sys.prefix}')"
```

### Set Up PostgreSQL (for full test suite)

```bash
# Create database and user
sudo -u postgres psql <<EOF
CREATE USER iceberg WITH PASSWORD 'iceberg';
CREATE DATABASE iceberg OWNER iceberg;
GRANT ALL PRIVILEGES ON DATABASE iceberg TO iceberg;
EOF

# Or on Windows/WSL:
psql -U postgres -c "CREATE USER iceberg WITH PASSWORD 'iceberg';"
psql -U postgres -c "CREATE DATABASE iceberg OWNER iceberg;"
psql -U postgres -c "GRANT ALL PRIVILEGES ON DATABASE iceberg TO iceberg;"

# Test connection
psql -h localhost -U iceberg -d iceberg -c "SELECT version();"
```

---

## Running Tests

### Quick Test (No PostgreSQL Required)

```bash
# Activate virtual environment first
source venv/bin/activate

# Run core tests (110 tests, no external dependencies)
pytest sentinel_os/Tests/ -v

# Run specific test file
pytest sentinel_os/Tests/test_sentinel_core.py -v
```

### Full Test Suite (Requires PostgreSQL)

```bash
# Make sure PostgreSQL is running and iceberg database exists
pytest sentinel_os/Tests/ -v --tb=short

# With coverage report
pytest sentinel_os/Tests/ --cov=sentinel_os --cov-report=html
# Open htmlcov/index.html to view coverage
```

### Run the Simulator

```bash
# Test with the standalone simulator (no database needed)
python3 sentinel_os/iceberg_complete_simulator.py
```

### Run with Docker Compose

```bash
cd sentinel_os
docker-compose up -d
# Services will be available at:
# - API: http://localhost:9090
# - PostgreSQL: localhost:5432
# - Grafana: http://localhost:3000
```

---

## Code Style & Conventions

We follow PEP 8 with a few guidelines:

### Python Style

```bash
# Install linting tools
pip install black flake8 isort

# Format code (Black)
black sentinel_os/

# Check for style issues (Flake8)
flake8 sentinel_os/ --max-line-length=100

# Sort imports
isort sentinel_os/
```

### Naming Conventions

- **Functions:** `snake_case` (e.g., `calculate_friction_score()`)
- **Classes:** `PascalCase` (e.g., `GovernanceDecisionRecord`)
- **Constants:** `UPPER_SNAKE_CASE` (e.g., `MAX_FRICTION_THRESHOLD`)
- **Private methods:** prefix with `_` (e.g., `_internal_helper()`)

### Code Comments

- Use clear, concise comments for complex logic
- Document functions with docstrings (Google style):

```python
def calculate_wait_time(queue_depth: int, agents_available: int) -> float:
    """
    Calculate expected wait time using Erlang C formula.
    
    Args:
        queue_depth: Number of calls waiting in queue
        agents_available: Number of available agents
        
    Returns:
        Expected wait time in seconds
        
    Raises:
        ValueError: If inputs are negative
    """
    if queue_depth < 0 or agents_available < 0:
        raise ValueError("Queue depth and agents must be non-negative")
    
    # Erlang C calculation here
    return wait_time
```

### No hardcoded values!

All governance parameters go in `cassettes/`. Never hardcode thresholds, timeouts, or policies directly in code.

---

## Git Workflow

### Create a Branch

Always create a new branch for your work:

```bash
# Update main branch first
git fetch upstream
git checkout main
git pull upstream main

# Create a new branch for your feature
git checkout -b fix/issue-description
# or
git checkout -b feature/new-feature-name
```

**Branch naming:**
- `feature/` for new features
- `fix/` for bug fixes
- `docs/` for documentation
- `refactor/` for code cleanup
- `test/` for tests

### Make Your Changes

```bash
# Make your edits
# Test frequently!
pytest sentinel_os/Tests/ -v

# Stage changes
git add .

# Commit with clear message
git commit -m "Fix: Correct governance decision timeout logic

- Increased timeout from 5s to 10s
- Added fallback to fail-closed on timeout
- Updated related tests
- Verified with load_test_live.py (942K calls/sec baseline maintained)"

# Push to your fork
git push origin fix/issue-description
```

### Commit Message Guidelines

**Format:** `Type: Brief description`

```
Type can be:
- Fix: Bug fix
- Feature: New feature
- Docs: Documentation changes
- Refactor: Code cleanup (no behavior change)
- Test: Adding/updating tests
- Chore: Dependency updates, config changes

Example:
Fix: Handle null caller_id in intent classification
Feature: Add retry logic to Claude API calls
Docs: Update ARCHITECTURE.md with new governance flow
```

### Create a Pull Request

1. Go to https://github.com/wking53214/sentinel_os
2. Click **"Compare & pull request"** (GitHub will suggest this)
3. Fill out the PR template:
   - **Title:** Clear, descriptive (e.g., "Fix: Correct governance decision timeout")
   - **Description:** Explain what changed and why
   - **Related issues:** Reference any issues this closes (e.g., "Closes #42")
   - **Testing:** Describe how you tested

```markdown
## Description
Fixed the governance decision timeout that was causing false rejections 
during high load. Previously set to 5 seconds, now 10 seconds with 
fail-closed fallback.

## Type of Change
- [x] Bug fix (non-breaking change that fixes an issue)
- [ ] New feature (non-breaking change that adds functionality)
- [ ] Breaking change
- [ ] Documentation update

## Testing
- [x] Unit tests pass (110/110)
- [x] Ran load_test_live.py - maintained 942K calls/sec
- [x] Manual testing with docker-compose setup

## Related Issues
Closes #42
```

---

## Testing Requirements

Before submitting a PR:

### 1. All tests must pass

```bash
pytest sentinel_os/Tests/ -v
```

### 2. No regressions in performance

```bash
python3 sentinel_os/load_test.py
# Should maintain ~942K calls/sec baseline
```

### 3. New code must include tests

If you add a new function, add a test for it:

```python
# sentinel_os/Tests/test_my_feature.py
import pytest
from sentinel_os.my_module import my_function

def test_my_function_returns_expected_value():
    result = my_function(input_data)
    assert result == expected_output

def test_my_function_handles_edge_cases():
    with pytest.raises(ValueError):
        my_function(invalid_input)
```

### 4. Code style checks pass

```bash
black sentinel_os/ --check
flake8 sentinel_os/
isort sentinel_os/ --check-only
```

---

## Reporting Bugs

Found a bug? Thanks for reporting it! Here's how:

1. **Check existing issues** - Search https://github.com/wking53214/sentinel_os/issues to see if it's already reported
2. **Create a new issue** with the following info:

```markdown
## Description
A clear description of what the bug is.

## Steps to Reproduce
1. Run command: `...`
2. Expected: `...`
3. Actual: `...`

## Environment
- OS: macOS / Linux / Windows
- Python version: 3.11
- Sentinel OS version: main branch

## Error Message
```
Paste full error traceback here
```

## Additional Context
Any other relevant information
```

---

## Suggesting Features

Have an idea? We'd love to hear it!

1. **Check existing issues** - Search for similar feature requests
2. **Create a discussion** or issue explaining:
   - What problem does it solve?
   - How would you use it?
   - Any alternative approaches?

---

## Documentation Contributions

Documentation improvements are just as valuable as code!

### Update Existing Docs

1. Find the `.md` file you want to improve
2. Make your edits
3. Test that markdown renders correctly on GitHub
4. Submit a PR with clear description of changes

### Add New Docs

If you think we need new documentation:

1. Discuss in an issue first
2. Follow the same structure as existing docs
3. Include code examples where relevant
4. Link to related documentation

---

## Getting Help

- **Questions?** Open a GitHub Discussion
- **Stuck?** Comment on the issue or PR
- **Need guidance?** Reach out to @wking53214
- **Found a security issue?** Email instead of posting publicly

---

## Review Process

Once you submit a PR:

1. **Automated checks** will run (tests, style checks)
2. **Code review** - Maintainer will review your changes
3. **Feedback** - You may be asked for clarifications or changes
4. **Approval** - PR is merged once approved
5. **Deployment** - Your contribution goes live! 🎉

### Tips for getting your PR approved faster

- Keep PRs focused (one feature or fix per PR)
- Write clear commit messages
- Include tests
- Follow code style guidelines
- Respond to feedback promptly
- Test thoroughly before submitting

---

## What We're Looking For

We especially welcome contributions in these areas:

- 🐛 **Bug fixes** - Found and fixed a bug?
- 📚 **Documentation** - Improved clarity or added examples?
- ✅ **Tests** - Added test coverage, especially for edge cases?
- 🚀 **Performance** - Optimized slow code paths?
- 🔧 **Tooling** - Improved development experience?
- 🌍 **Localization** - Translated docs or messages?

---

## Recognition

We recognize and celebrate our contributors! Every PR that's merged will be noted in:
- Release notes
- GitHub contributors page
- Project README (pending - we'll add this!)

---

## License

By contributing to Sentinel OS, you agree that your contributions will be licensed under the same license as the project. (Add your license here - MIT, Apache 2.0, etc.)

---

## Questions?

Don't hesitate to ask! Contributing should be fun and rewarding. We're here to help you succeed.

- **GitHub Issues:** https://github.com/wking53214/sentinel_os/issues
- **Discussions:** https://github.com/wking53214/sentinel_os/discussions
- **Email:** Contact the maintainer

Thank you for contributing to Sentinel OS! 🚀
