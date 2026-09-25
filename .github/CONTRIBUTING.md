# Contributing to QuarterBot

Contributions are welcome! Here are some guidelines for how you can help.

## Reporting issues

1. First, check whether the problem has already been reported under the repository's Issues tab.
2. If not, open a new issue using the bug report template. It asks for:
   - A clear, descriptive title
   - A detailed description of the problem
   - Steps to reproduce it
   - Expected vs. actual behavior
   - Relevant logs or error messages
   - Screenshots, if relevant

**Never post secrets** in an issue or pull request: no Discord bot token, `ESPN_S2`, `SWID`, Google service-account credentials or `.env` contents. Check logs before pasting them.

## Suggesting changes

1. Fork the repository
2. Create a new branch from `main`
3. Make your changes
4. Write tests that cover them
5. Run the checks below
6. Commit with short, descriptive messages
7. Push to your fork
8. Open a pull request against the main repository and fill in the template

## Setup

Install everything, including the development tools, with:

```bash
pip install -r requirements.txt
```

## Code style

- Use type hints for all functions and methods
- Write docstrings for all classes and functions
- Follow [PEP 8](https://peps.python.org/pep-0008/)
- Format your code with `black .`
- Write code comments, docstrings, log messages and commit messages in English

## Checks

Run these before opening a pull request. CI runs the same checks (except `black`) on every pull request.

```bash
black .
ruff check .
mypy .
python -m pytest
```

## Testing

- Write tests for all new functionality
- Maintain or improve test coverage

## Questions?

If you have questions about how to contribute, open an issue and describe what you're wondering about.
