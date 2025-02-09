.NOTPARALLEL:

export POETRY_HOME=$(CURDIR)/.poetry
# https://github.com/python-poetry/poetry/issues/1917
export PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring
POETRY:=$(POETRY_HOME)/bin/poetry
POETRY_VENV=$(CURDIR)/.venv
POETRY_DEPS:=$(POETRY_VENV)/.deps
SYS_PYTHON:=$(shell env PATH='/bin:/usr/bin:/usr/local/bin:$(PATH)' bash -c "command -v python3.13 || command -v python3.12 || command -v python3.11 || command -v python3.10 || command -v python3.9 || echo .python-not-found")
export PYTHONPATH=$(CURDIR)/bin

.PHONY: help
help: # with thanks to Ben Rady
	@grep -E '^[0-9a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

PACKER ?= packer

$(SYS_PYTHON):
	@echo "Python 3.9, 3.10, 3.11, 3.12 or 3.13 not found on path. Please install (sudo apt install python3 or similar)"
	@exit 1

config.json: make_json.py | $(POETRY_DEPS)
	$(POETRY) run python make_json.py

.PHONY: packer
packer: config.json ## Builds the base image for compiler explorer nodes
	$(PACKER) build -timestamp-ui -var-file=config.json $(EXTRA_ARGS) packer/node.pkr.hcl

.PHONY: packer-local
packer-local: config.json ## Builds a local docker version of the compiler explorer node image
	$(PACKER) build -timestamp-ui -var-file=config.json $(EXTRA_ARGS) packer/local.pkr.hcl

.PHONY: packer-admin
packer-admin: config.json  ## Builds the base image for the admin server
	$(PACKER) build -timestamp-ui -var-file=config.json $(EXTRA_ARGS) packer/admin.pkr.hcl

.PHONY: packer-conan
packer-conan: config.json  ## Builds the base image for the CE conan-server
	$(PACKER) build -timestamp-ui -var-file=config.json $(EXTRA_ARGS) packer/conan.pkr.hcl

.PHONY: packer-gpu-node
packer-gpu-node: config.json  ## Builds the base image for the CE gpu nodes
	$(PACKER) build -timestamp-ui -var-file=config.json $(EXTRA_ARGS) packer/gpu-node.pkr.hcl

.PHONY: packer-aarch64-node
packer-aarch64-node: config.json  ## Builds the base image for the CE aarch64 nodes
	$(PACKER) build -timestamp-ui -var-file=config.json $(EXTRA_ARGS) packer/aarch64-node.pkr.hcl

.PHONY: packer-smb
packer-smb: config.json  ## Builds the base image for the CE smb-server
	$(PACKER) build -timestamp-ui -var-file=config.json $(EXTRA_ARGS) packer/smb.pkr.hcl

.PHONY: packer-smb-local
packer-smb-local: config.json  ## Builds the base image for the CE smb-server for local testing
	$(PACKER) build -timestamp-ui -var-file=config.json $(EXTRA_ARGS) packer/smb-local.pkr.hcl

.PHONY: packer-win
packer-win: config.json  ## Builds the base image for the CE windows
	$(PACKER) build -timestamp-ui -var-file=config.json $(EXTRA_ARGS) packer/win.pkr.hcl

.PHONY: packer-builder
packer-builder: config.json  ## Builds the base image for the CE builder
	$(PACKER) build -timestamp-ui -var-file=config.json $(EXTRA_ARGS) packer/builder.pkr.hcl

.PHONY: packer-win-builder
packer-win-builder: config.json  ## Builds the base image for the CE Windows builder
	$(PACKER) build -timestamp-ui -var-file=config.json $(EXTRA_ARGS) packer/win-builder.pkr.hcl

.PHONY: clean
clean:  ## Cleans up everything
	rm -rf $(POETRY_HOME) $(POETRY_VENV)

.PHONY: update-admin
update-admin:  ## Updates the admin website
	aws s3 sync admin/ s3://compiler-explorer/admin/ --cache-control max-age=5 --metadata-directive REPLACE

.PHONY: ce
ce: $(POETRY) $(POETRY_DEPS)  ## Installs and configures the python environment needed for the various admin commands
$(POETRY): $(SYS_PYTHON) poetry.toml
	curl -sSL https://install.python-poetry.org | $(SYS_PYTHON) -
	@touch $@

poetry.lock:
	$(POETRY) lock

$(POETRY_DEPS): $(POETRY) pyproject.toml poetry.lock
	$(POETRY) sync --no-root
	@touch $@

PY_SOURCE_ROOTS:=bin/lib bin/test lambda

.PHONY: test
test: ce  ## Runs the tests
	$(POETRY) run pytest $(PY_SOURCE_ROOTS)

.PHONY: static-checks
static-checks: ce  ## Runs all the static tests
	env SKIP=test $(POETRY) run pre-commit run --all-files

LAMBDA_PACKAGE_DIR:=$(CURDIR)/.dist/lambda-package
LAMBDA_PACKAGE:=$(CURDIR)/.dist/lambda-package.zip
LAMBDA_PACKAGE_SHA:=$(CURDIR)/.dist/lambda-package.zip.sha256
$(LAMBDA_PACKAGE): $(PYTHON) $(wildcard lambda/*) Makefile
	rm -rf $(LAMBDA_PACKAGE_DIR)
	mkdir -p $(LAMBDA_PACKAGE_DIR)
	$(POETRY) run python -mpip install -r lambda/requirements.txt -t $(LAMBDA_PACKAGE_DIR)
	cp -R lambda/* $(LAMBDA_PACKAGE_DIR)
	rm -f $(LAMBDA_PACKAGE)
	cd $(LAMBDA_PACKAGE_DIR) && zip -r $(LAMBDA_PACKAGE) .

EVENTS_LAMBDA_PACKAGE_DIR:=$(CURDIR)/.dist/events-lambda-package
EVENTS_LAMBDA_PACKAGE:=$(CURDIR)/.dist/events-lambda-package.zip
EVENTS_LAMBDA_PACKAGE_SHA:=$(CURDIR)/.dist/events-lambda-package.zip.sha256
EVENTS_LAMBDA_DIR:=$(CURDIR)/events-lambda
$(EVENTS_LAMBDA_PACKAGE):
	rm -rf $(EVENTS_LAMBDA_PACKAGE_DIR)
	mkdir -p $(EVENTS_LAMBDA_PACKAGE_DIR)
	cd $(EVENTS_LAMBDA_DIR) && npm i && npm run lint && npm install --no-audit --ignore-scripts --production && npm install --no-audit --ignore-scripts --production --cpu arm64 && cd ..
	cp -R $(EVENTS_LAMBDA_DIR)/* $(EVENTS_LAMBDA_PACKAGE_DIR)
	rm -f $(EVENTS_LAMBDA_PACKAGE)
	cd $(EVENTS_LAMBDA_PACKAGE_DIR) && zip -r $(EVENTS_LAMBDA_PACKAGE) .

$(LAMBDA_PACKAGE_SHA): $(LAMBDA_PACKAGE)
	openssl dgst -sha256 -binary $(LAMBDA_PACKAGE) | openssl enc -base64 > $@

$(EVENTS_LAMBDA_PACKAGE_SHA): $(EVENTS_LAMBDA_PACKAGE)
	openssl dgst -sha256 -binary $(EVENTS_LAMBDA_PACKAGE) | openssl enc -base64 > $@

.PHONY: lambda-package  ## builds lambda
lambda-package: $(LAMBDA_PACKAGE) $(LAMBDA_PACKAGE_SHA)

.PHONY: upload-lambda
upload-lambda: lambda-package  ## Uploads lambda to S3
	aws s3 cp $(LAMBDA_PACKAGE) s3://compiler-explorer/lambdas/lambda-package.zip
	aws s3 cp --content-type text/sha256 $(LAMBDA_PACKAGE_SHA) s3://compiler-explorer/lambdas/lambda-package.zip.sha256

.PHONY: events-lambda-package  ## Builds events-lambda
events-lambda-package: $(EVENTS_LAMBDA_PACKAGE) $(EVENTS_LAMBDA_PACKAGE_SHA)

.PHONY: upload-events-lambda
upload-events-lambda: events-lambda-package  ## Uploads events-lambda to S3
	aws s3 cp $(EVENTS_LAMBDA_PACKAGE) s3://compiler-explorer/lambdas/events-lambda-package.zip
	aws s3 cp --content-type text/sha256 $(EVENTS_LAMBDA_PACKAGE_SHA) s3://compiler-explorer/lambdas/events-lambda-package.zip.sha256

.PHONY: terraform-apply
terraform-apply:  ## Applies terraform
	cd terraform && terraform apply

.PHONY: pre-commit
pre-commit: ce  ## Runs all pre-commit hooks
	$(POETRY) run pre-commit run --all-files

.PHONY: install-pre-commit
install-pre-commit: ce  ## Install pre-commit hooks
	$(POETRY) run pre-commit install
