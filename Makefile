# Convenience targets for purpleair-local development.
#
# `make ha-up` boots a disposable Home Assistant in Docker with this
# repo's custom_components/purpleair_local/ mounted into /config. HA
# is reachable at http://localhost:8123; on first boot it walks you
# through user creation, then you can add the integration from
# Settings → Devices & Services → Add Integration → "PurpleAir Local".

.PHONY: ha-init ha-up ha-down ha-logs ha-restart ha-reset test

# Test targets ------------------------------------------------------------

test:
	.venv/bin/pytest

# Dev HA testbed ----------------------------------------------------------

ha-init:
	@mkdir -p .dev/ha-config
	@if [ ! -f .dev/ha-config/configuration.yaml ]; then \
		cp .dev/configuration.yaml.example .dev/ha-config/configuration.yaml; \
		echo "seeded .dev/ha-config/configuration.yaml from example"; \
	fi

ha-up: ha-init
	docker compose up -d
	@echo ""
	@echo "HA starting at http://localhost:8123 (first boot ~1 min)"
	@echo "Tail logs: make ha-logs"

ha-down:
	docker compose down

ha-restart:
	docker compose restart ha

ha-logs:
	docker compose logs -f ha

# Wipe runtime config — use after changing this integration's storage
# shape or just to start over from the onboarding screen.
ha-reset:
	docker compose down
	rm -rf .dev/ha-config
	@echo "HA config wiped. Run 'make ha-up' for a fresh instance."
