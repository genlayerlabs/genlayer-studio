# Integration Tests

This directory contains integration tests for the GenLayer Studio project. These tests verify that different components of the system work together correctly.

## Prerequisites

- `gltest` command-line tool installed
- Access to the required contracts directory

## Setup

1. Ensure you have all dependencies installed:
   ```sh
   pip install -r requirements.txt
   pip install -r requirements.test.txt
   ```

2. Make sure you have the necessary intelligent contracts in your workspace

## Running Tests

From the root of the repository, run the following command:

```sh
gltest --contracts-dir . tests/integration/ -svv
```

### Command Options

- `--contracts-dir .`: Specifies the directory containing the contract files
- `-svv`: Verbose output flag for detailed test information

### Test Structure

The integration tests are organized to test:
- Contract interactions
- RPC endpoints

## Troubleshooting

If you encounter issues:
1. Verify that all dependencies are installed
2. Check that the contracts directory is properly configured


## Contributing

When adding new integration tests:
1. Follow the existing test structure
2. Include clear test descriptions
3. Add appropriate assertions
4. Document any special setup requirements