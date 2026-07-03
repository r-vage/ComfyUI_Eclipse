#!/bin/bash

# Script to setup swap file for Linux
# Default: 96GB (adjust SWAP_SIZE_GB variable as needed)

# Configuration
SWAP_SIZE_GB=96  # Change this to desired size in GB
SWAPFILE_PATH="/swapfile"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if running as root, if not, re-run with sudo
if [ "$EUID" -ne 0 ]; then 
    echo "This script requires root privileges. Re-running with sudo..."
    exec sudo "$0" "$@"
fi

echo -e "${GREEN}=== Swap File Setup Script ===${NC}"
echo -e "Target Size: ${YELLOW}${SWAP_SIZE_GB}GB${NC}"
echo -e "Swap File Path: ${YELLOW}${SWAPFILE_PATH}${NC}"
echo ""

# Calculate size in MB
SWAP_SIZE_MB=$((SWAP_SIZE_GB * 1024))

echo -e "${YELLOW}Step 1:${NC} Turning off all swap files..."
swapoff -a
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Swap turned off${NC}"
else
    echo -e "${RED}✗ Failed to turn off swap${NC}"
    exit 1
fi

echo ""
echo -e "${YELLOW}Step 2:${NC} Removing old swap file (if exists)..."
if [ -f "$SWAPFILE_PATH" ]; then
    rm "$SWAPFILE_PATH"
    echo -e "${GREEN}✓ Old swap file removed${NC}"
else
    echo -e "${YELLOW}No existing swap file found, skipping...${NC}"
fi

echo ""
echo -e "${YELLOW}Step 3:${NC} Creating new ${SWAP_SIZE_GB}GB swap file..."
echo -e "${YELLOW}This may take a few minutes...${NC}"
dd if=/dev/zero of="$SWAPFILE_PATH" bs=1M count="$SWAP_SIZE_MB" status=progress
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Swap file created${NC}"
else
    echo -e "${RED}✗ Failed to create swap file${NC}"
    exit 1
fi

echo ""
echo -e "${YELLOW}Step 4:${NC} Setting permissions..."
chmod 600 "$SWAPFILE_PATH"
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Permissions set${NC}"
else
    echo -e "${RED}✗ Failed to set permissions${NC}"
    exit 1
fi

echo ""
echo -e "${YELLOW}Step 5:${NC} Making swap file..."
mkswap "$SWAPFILE_PATH"
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Swap file formatted${NC}"
else
    echo -e "${RED}✗ Failed to format swap file${NC}"
    exit 1
fi

echo ""
echo -e "${YELLOW}Step 6:${NC} Enabling swap..."
swapon "$SWAPFILE_PATH"
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Swap enabled${NC}"
else
    echo -e "${RED}✗ Failed to enable swap${NC}"
    exit 1
fi

echo ""
echo -e "${YELLOW}Step 7:${NC} Making swap permanent..."
# Check if entry already exists in fstab
if grep -q "$SWAPFILE_PATH" /etc/fstab; then
    echo -e "${YELLOW}Swap entry already exists in /etc/fstab${NC}"
else
    echo "$SWAPFILE_PATH none swap sw 0 0" >> /etc/fstab
    echo -e "${GREEN}✓ Added to /etc/fstab${NC}"
fi

# Re-enable the partition swap if it exists
echo ""
echo -e "${YELLOW}Step 8:${NC} Re-enabling partition swap (if exists)..."
swapon -a
echo -e "${GREEN}✓ All swap enabled${NC}"

echo ""
echo -e "${GREEN}=== Swap Setup Complete! ===${NC}"
echo ""
echo -e "${YELLOW}Current Swap Status:${NC}"
swapon --show
echo ""
free -h
echo ""
echo -e "${GREEN}Done! Your system now has additional swap space.${NC}"
