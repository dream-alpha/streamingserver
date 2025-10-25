#!/usr/bin/env python3
# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
Simple Test Runner for Streaming Server

Run tests with proper Python path configuration.
No complex dependencies - just pytest with basic setup.
"""

import os
import sys
import subprocess
from pathlib import Path

# Get project root directory
PROJECT_ROOT = Path(__file__).parent
SRC_DIR = PROJECT_ROOT / "src"
TESTS_DIR = PROJECT_ROOT / "tests"

def setup_python_path():
    """Setup Python path to include src directory"""
    src_path = str(SRC_DIR.absolute())

    # Add to Python path if not already there
    if src_path not in sys.path:
        sys.path.insert(0, src_path)

    # Set PYTHONPATH environment variable
    current_pythonpath = os.environ.get('PYTHONPATH', '')
    if src_path not in current_pythonpath:
        if current_pythonpath:
            os.environ['PYTHONPATH'] = f"{src_path}:{current_pythonpath}"
        else:
            os.environ['PYTHONPATH'] = src_path

def check_dependencies():
    """Check that required testing dependencies are available"""
    required_packages = ['pytest']
    missing_packages = []

    for package in required_packages:
        try:
            __import__(package)
        except ImportError:
            missing_packages.append(package)

    if missing_packages:
        print(f"Missing required packages: {', '.join(missing_packages)}")
        print("Install with: pip install " + " ".join(missing_packages))
        return False

    return True

def check_coverage_available():
    """Check if pytest-cov is available for coverage reports"""
    try:
        import pytest_cov
        return True
    except ImportError:
        return False

def run_tests(test_pattern=None, verbose=False, coverage=False):
    """Run tests with pytest"""
    # Setup environment
    setup_python_path()

    # Check dependencies
    if not check_dependencies():
        return False

    # Check coverage availability if requested
    if coverage and not check_coverage_available():
        print("⚠️  Coverage requested but pytest-cov not installed")
        print("   Install with: pip install pytest-cov")
        print("   Running tests without coverage...")
        coverage = False

    # Build pytest command
    cmd = ['python', '-m', 'pytest']

    if verbose:
        cmd.append('-v')

    if coverage:
        cmd.extend(['--cov=streamingserver', '--cov-report=term', '--cov-report=html'])

    # Add test directory or specific pattern
    if test_pattern:
        test_path = TESTS_DIR / test_pattern
        if test_path.exists():
            cmd.append(str(test_path))
        else:
            cmd.append(test_pattern)
    else:
        cmd.append(str(TESTS_DIR))

    # Run tests
    print(f"Running: {' '.join(cmd)}")
    print(f"Working directory: {PROJECT_ROOT}")
    print(f"Python path includes: {SRC_DIR}")
    print("-" * 60)

    try:
        result = subprocess.run(cmd, cwd=PROJECT_ROOT, check=False)

        if coverage and result.returncode == 0:
            print("\n📊 Coverage report generated in htmlcov/index.html")

        return result.returncode == 0
    except FileNotFoundError:
        print("Error: Python not found in PATH")
        return False
    except Exception as e:
        print(f"Error running tests: {e}")
        return False

def run_specific_test_suite(suite_name):
    """Run a specific test suite"""
    test_files = {
        'core': 'test_unit_core.py',
        'recorders': 'test_unit_recorders.py',
        'providers': 'test_unit_providers.py',
        'integration': 'test_integration_basic.py',
        'basic': 'test_basic.py'
    }

    if suite_name in test_files:
        return run_tests(test_files[suite_name], verbose=True)
    else:
        print(f"Unknown test suite: {suite_name}")
        print(f"Available suites: {', '.join(test_files.keys())}")
        return False

def show_test_info():
    """Show information about available tests"""
    print("Streaming Server Test Suite")
    print("=" * 50)
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Source directory: {SRC_DIR}")
    print(f"Tests directory: {TESTS_DIR}")
    print()

    print("Available test files:")
    test_files = list(TESTS_DIR.glob("test_*.py"))
    for test_file in sorted(test_files):
        print(f"  - {test_file.name}")
    print()

    print("Test suites:")
    suites = {
        'core': 'Core module tests (config, debug, utils)',
        'recorders': 'Recorder functionality tests',
        'providers': 'Provider and resolver tests',
        'integration': 'Basic integration tests',
        'basic': 'Basic functionality tests'
    }

    for suite, description in suites.items():
        print(f"  - {suite}: {description}")
    print()

    print("Usage examples:")
    print("  python run_tests.py                    # Run all tests")
    print("  python run_tests.py --suite core       # Run core tests")
    print("  python run_tests.py --verbose          # Run with verbose output")
    print("  python run_tests.py --coverage         # Run with coverage report")
    print("  python run_tests.py --info             # Show this information")
    print("  python run_tests.py --check-deps       # Check dependencies")
    print()
    print("Coverage note: Low coverage is expected for unit tests as they focus on")
    print("interfaces and mocked functionality rather than full execution paths.")

def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description="Run streaming server tests")
    parser.add_argument('--suite', help='Run specific test suite')
    parser.add_argument('--pattern', help='Run tests matching pattern')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    parser.add_argument('--coverage', '-c', action='store_true', help='Generate coverage report')
    parser.add_argument('--info', action='store_true', help='Show test information')
    parser.add_argument('--check-deps', action='store_true', help='Check test dependencies')

    args = parser.parse_args()

    # Change to project directory
    os.chdir(PROJECT_ROOT)

    if args.info:
        show_test_info()
        return

    if args.check_deps:
        if check_dependencies():
            print("All required dependencies are available")
        else:
            print("Some dependencies are missing")
        return

    if args.suite:
        success = run_specific_test_suite(args.suite)
    elif args.pattern:
        success = run_tests(args.pattern, args.verbose, args.coverage)
    else:
        success = run_tests(verbose=args.verbose, coverage=args.coverage)

    if success:
        print("\n✅ Tests completed successfully!")
    else:
        print("\n❌ Tests failed or had errors")
        sys.exit(1)

if __name__ == "__main__":
    main()
