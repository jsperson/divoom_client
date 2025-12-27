# Divoom Client

A display manager for Divoom Pixoo 64 LED displays.

## Installation

```bash
uv venv
source .venv/bin/activate
uv pip install -e .
```

## Usage

```bash
# Discover devices on the network
divoom discover

# Test connection
divoom test

# Set brightness
divoom brightness 50

# Clear display
divoom clear --color "#FF0000"
```

## Configuration

Create `config/device.json` with your Pixoo IP:

```json
{
  "ip_address": "192.168.1.100",
  "brightness": 100
}
```
