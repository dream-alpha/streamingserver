#!/usr/bin/bash

# Exit immediately if a command fails or if an undefined variable is used.
set -euxo pipefail

# Update package index
sudo apt update

# Install required dependencies
# sudo apt install -y \
