PYTHON ?= python3
PORT ?= 8080
LAYOUT ?= config/layouts/dashboard.json
IMAGE ?= divoom-client:local

.PHONY: install run preview test lint render-examples docker-build docker-run service-install

install:
	./scripts/install.sh

run:
	. .venv/bin/activate && divoom serve $(LAYOUT) --web --port $(PORT) --no-device

preview:
	. .venv/bin/activate && divoom render $(LAYOUT) --data config/sample_data.demo.json --output docs/images/layout-preview.png

test:
	. .venv/bin/activate && pytest -q

lint:
	. .venv/bin/activate && ruff check src/divoom_client/web tests

render-examples:
	mkdir -p docs/images
	. .venv/bin/activate && divoom render config/layouts/dashboard.json --data config/sample_data.demo.json --output docs/images/dashboard-preview.png
	. .venv/bin/activate && divoom render config/layouts/weather-clock.json --data config/sample_data.demo.json --output docs/images/weather-clock-preview.png
	. .venv/bin/activate && divoom render config/layouts/markets.json --data config/sample_data.demo.json --output docs/images/markets-preview.png

docker-build:
	docker build -t $(IMAGE) .

docker-run:
	docker run --rm -p $(PORT):8080 -v "$$(pwd)/config:/app/config" $(IMAGE)

service-install:
	INSTALL_SERVICE=1 ./scripts/install.sh
