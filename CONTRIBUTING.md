# Contributing to GenLayer Studio

We're thrilled that you're interested in contributing to the GenLayer Studio! This document will guide you through the contribution process.

## What is the GenLayer Studio?

The GenLayer Studio is an interactive sandbox designed for developers to explore the potential of GenLayer's Intelligent Contracts. It replicates the GenLayer network's execution environment and consensus algorithm, providing a controlled and local environment to test different ideas and behaviors.

## How You Can Contribute?

Contributions to the GenLayer Studio are welcome in several forms:

### Testing the Studio and Providing Feedback

Help us make the Studio better by testing and giving feedback:

- Start installing the Studio using the GenLayer [CLI](https://github.com/yeagerai/genlayer-simulator?tab=readme-ov-file#quick-install).
- Try out the Studio features and tell us what you think through our [feedback form](https://docs.google.com/forms/d/1IVNsZwm936kSNCiXmlAP8bgJnbik7Bqaoc3I6UYhr-o/viewform) or on our [Discord Channel](https://discord.gg/8Jm4v89VAu).
- If you find any issues, please report them on our [GitHub issues page](https://github.com/yeagerai/genlayer-simulator/issues).

### Sharing New Ideas and Use Cases

Have ideas for new features or use cases? We're eager to hear them! But first:

- Ensure you have the [Studio installed](https://github.com/yeagerai/genlayer-simulator?tab=readme-ov-file#quick-install) first to explore existing use cases.
- After familiarizing yourself with the Studio, contribute your unique use case and share your ideas in our [Discord channel](https://discord.gg/8Jm4v89VAu).

### Bug fixing and Feature development

#### 1. Set yourself up to start coding

- **1.1. Pick an issue**: Select one from the project GitHub repository [issue list](https://github.com/yeagerai/genlayer-simulator/issues) and assign it to yourself.

- **1.2. Create a branch**: create the branch that you will work on by using the link provided in the issue details page (right panel at the bottom - section "Development")

- **1.3. Setup the Studio locally**: launch the Studio's frontend and backend by running the docker compose command (Please note that you must have docker, node, and npm installed)

   ```sh
   $ cp .env.example .env
   $ docker compose up
   ```

- **1.4. Running Tests**:

   ```sh
   # DB/SQLAlchemy tests (dockerized, self-contained - primary backend tests)
   $ docker compose -f tests/db-sqlalchemy/docker-compose.yml --project-directory . run --build --rm tests

   # Backend unit tests (requires local venv)
   $ python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
   $ .venv/bin/pytest tests/unit/ -v --tb=short --ignore=tests/unit/test_rpc_endpoint_manager.py

   # Integration tests (requires venv + running containers)
   $ .venv/bin/gltest --contracts-dir . tests/integration/ -svv

   # Frontend tests
   $ cd frontend && npm run test      # Unit tests (Vitest)
   $ cd frontend && npm run test:e2e  # E2E tests (Mocha/Selenium)
   ```

#### 2. Submit your solution
- **2.1. Install [pre-commit](https://pre-commit.com) hooks**: this is used for linting, testing, and enforcing conventions.

   ```sh
   (.venv) $ pre-commit install
   ```

   To look into the configuration details, check the `.pre-commit-config.yaml` file.

   Some of the configurations applied are:

   - Python Black formatter
   - Python pytest for unit tests
   - ESLint for frontend code
   - Prettier for frontend code
   - [Conventional commits](https://www.conventionalcommits.org/en/v1.0.0/) for commit messages

- **2.2. Black Formatter on Save File**: Configure IDE extensions to format your code with [Black](https://github.com/psf/black) before submitting it.
- **2.3. Code solution**: implement the solution in the code.
- **2.4. Pull Request**: Submit your changes through a pull request (PR). Fill the entire PR template and set the PR title as a valid conventional commit.
- **2.5. Check PR and issue linking**: if the issue and the PR are not linked, you can do it manually in the right panel of the Pull Request details page.
- **2.6. PR Validation and Testing**: Your PR will be automatically validated, analyzed, and tested. Please make sure all tests and validations pass before proceeding.
- **2.7. Peer Review**: One or more core contributors will review your PR. They may suggest changes or improvements.
- **2.8. Approval and Merge**: After approval from the reviewers, you can merge your PR with a squash and merge type of action.

#### 3. Other considerations
- **3.1. Small fixes don't require creating an issue**: significantly small issues can be submitted through a valid Pull Request without needing to create an issue.
- **3.2. Run the frontend in dev mode**: to run the frontend separately from the backend and with hot reload enabled, first launch the backend without the frontend:

   ```sh
   $ docker compose upjsonrpc webrequest ollama database-migration postgres
   ```
   Then launch the frontend in dev mode:
   ```sh
   $ cd frontend
   $ npm install
   $ npm run dev
   ```
### Improving Documentation

To contribute to our docs, visit our [Documentation Repository](https://github.com/yeagerai/genlayer-docs) to create new issues or contribute to existing issues.


## Community

Connect with the GenLayer community to discuss, collaborate, and share insights:

- **[Discord Channel](https://discord.gg/8Jm4v89VAu)**: Our primary hub for discussions, support, and announcements.
- **[Telegram Group](https://t.me/genlayer)**: For more informal chats and quick updates.

Your continuous feedback drives better product development. Please engage with us regularly to test, discuss, and improve the GenLayer Studio.
