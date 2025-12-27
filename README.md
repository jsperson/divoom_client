# Divoom Pixoo 64 Client

A display manager for Divoom Pixoo 64 LED displays with a web-based layout editor, live data sources, and scheduled updates.

## Features

- **Web UI** - Browser-based dashboard and layout editor
- **Layout Editor** - Visual drag-and-drop editor with undo/redo support
- **Live Data Sources** - Stock prices (via Yahoo Finance) and weather (via OpenWeatherMap)
- **Conditional Colors** - Change widget colors based on data values
- **Device Discovery** - Automatically find Pixoo devices on your network
- **Scheduled Updates** - Auto-refresh data at configurable intervals
- **CLI Tools** - Command-line interface for scripting and automation

## Installation

### Prerequisites

- Python 3.10 or higher
- A Divoom Pixoo 64 device on your local network

### Using uv (recommended)

```bash
git clone https://github.com/jsperson/divoom_client.git
cd divoom_client
uv venv
source .venv/bin/activate
uv pip install -e .
```

### Using pip

```bash
git clone https://github.com/jsperson/divoom_client.git
cd divoom_client
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Quick Start

### 1. Discover your device

```bash
divoom discover
```

This will scan your network and display the IP address of any Pixoo devices found.

### 2. Configure your device

Create or edit `config/device.json`:

```json
{
  "ip_address": "192.168.1.100",
  "brightness": 100
}
```

### 3. Start the web server

```bash
divoom serve config/layouts/dashboard.json --web
```

Then open http://localhost:8080 in your browser.

## Web Interface

The web interface has several tabs:

### Dashboard
- View current display preview
- See data source status
- Quick refresh controls

### Layout Editor
- Visual canvas editor (64x64 pixels, scaled 8x)
- Add text, rectangles, and lines
- Edit widget properties (position, color, font, data binding)
- Undo/Redo support (Ctrl+Z / Ctrl+Y)
- Save and load layouts

### Data Sources
- Configure stock symbols and weather locations
- Enable/disable individual sources
- Test data fetching

### Device
- View device information
- Control power and brightness
- Switch display channels
- Scan network for devices

### Quick Actions
- Send quick text messages
- Clear display with solid color
- Activate preset layouts

## CLI Commands

```bash
divoom --help              # Show all commands

# Device control
divoom discover            # Find Pixoo devices on network
divoom test                # Test device connection
divoom brightness 50       # Set brightness (0-100)
divoom on                  # Turn display on
divoom off                 # Turn display off
divoom clear --color "#FF0000"  # Clear with color

# Layouts
divoom render layout.json  # Render layout to device
divoom live layout.json    # Render with live data
divoom demo                # Show demo pattern

# Server
divoom serve layout.json --web --port 8080  # Start web server
divoom status              # Show data source status
divoom fetch               # Fetch data from all sources
```

## Configuration

### Device Configuration (`config/device.json`)

```json
{
  "ip_address": "192.168.1.100",
  "brightness": 100
}
```

### Data Sources (`config/datasources.json`)

```json
{
  "sources": {
    "stocks": {
      "type": "stocks",
      "symbols": ["AAPL", "GOOGL", "MSFT"],
      "refresh_seconds": 300,
      "enabled": true
    },
    "weather": {
      "type": "weather",
      "api_key": "your_openweathermap_api_key",
      "location": "City,State,Country",
      "units": "imperial",
      "refresh_seconds": 600,
      "enabled": true
    }
  }
}
```

## Layout Format

Layouts are JSON files defining widgets to display:

```json
{
  "name": "my_layout",
  "background": "#000000",
  "refresh_seconds": 300,
  "widgets": [
    {
      "type": "text",
      "x": 2,
      "y": 2,
      "font": "5x7",
      "text": "Hello",
      "color": "#FFFFFF"
    },
    {
      "type": "text",
      "x": 2,
      "y": 12,
      "font": "4x6",
      "data_source": "stocks.AAPL.price",
      "format": "${value:.2f}",
      "color": "#00FF00"
    },
    {
      "type": "rect",
      "x": 0,
      "y": 60,
      "width": 64,
      "height": 4,
      "color": "#0000FF",
      "filled": true
    },
    {
      "type": "line",
      "x1": 0,
      "y1": 32,
      "x2": 63,
      "y2": 32,
      "color": "#333333"
    }
  ]
}
```

### Widget Types

#### Text Widget
- `x`, `y` - Position (0-63)
- `font` - "5x7" or "4x6"
- `text` - Static text content
- `data_source` - Dynamic data path (e.g., "stocks.AAPL.price")
- `format` - Python format string (e.g., "${value:.2f}")
- `color` - Hex color or conditional color object

#### Rectangle Widget
- `x`, `y` - Position
- `width`, `height` - Dimensions
- `color` - Hex color
- `filled` - true/false

#### Line Widget
- `x1`, `y1` - Start point
- `x2`, `y2` - End point
- `color` - Hex color

### Conditional Colors

```json
{
  "color": {
    "conditions": [
      { "when": "stocks.AAPL.change < 0", "color": "#FF0000" },
      { "when": "stocks.AAPL.change >= 0", "color": "#00FF00" }
    ],
    "default": "#FFFFFF"
  }
}
```

### Data Source Paths

- **Stocks**: `stocks.{SYMBOL}.price`, `stocks.{SYMBOL}.change`, `stocks.{SYMBOL}.percent`
- **Weather**: `weather.temp`, `weather.temp_min`, `weather.temp_max`, `weather.main`, `weather.humidity`

## Development

```bash
# Install dev dependencies
uv pip install -e ".[dev]"

# Run tests
pytest

# Type checking
mypy src/

# Linting
ruff check src/
```

## License

MIT License - see LICENSE file for details.

## Author

Jason Person (jsperson@gmail.com)
