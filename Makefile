# make run                      start the transcript server + translator (Claude API)
# make cc                       same, but translate through Claude Code on your Claude plan
# make run NOTES=notes.txt MODEL=claude-sonnet-5-5
# make server / make translate  run either half on its own

PY         := .venv/bin/python
BACKEND    ?= api
MODEL      ?= claude-opus-5-5
EFFORT     ?= low
TRANSCRIPT ?= transcript.txt
OUT        ?= translation.txt
HOST       ?= 127.0.0.1
NOTES      ?=

SERVER_ARGS    := --out $(TRANSCRIPT) --host $(HOST)
TRANSLATE_ARGS := --backend $(BACKEND) --model $(MODEL) --effort $(EFFORT) \
                  --transcript $(TRANSCRIPT) --out $(OUT) $(if $(NOTES),--notes $(NOTES))

.PHONY: run cc server translate install clean

# The server logs to server.log so the terminal shows only translations
# (`tail -f server.log` in another terminal to watch raw captions).
run: install
	@trap 'kill $$server 2>/dev/null' EXIT INT TERM; \
	$(PY) -u server.py $(SERVER_ARGS) > server.log 2>&1 & server=$$!; \
	sleep 0.5; kill -0 $$server 2>/dev/null || { cat server.log; exit 1; }; \
	echo "Transcript server running (log: server.log)"; \
	$(PY) translate.py $(TRANSLATE_ARGS)

cc:
	@$(MAKE) --no-print-directory run BACKEND=claude-code

server: install
	$(PY) server.py $(SERVER_ARGS)

translate: install
	$(PY) translate.py $(TRANSLATE_ARGS)

install: .venv/.installed

.venv/.installed: requirements.txt
	python3 -m venv .venv
	$(PY) -m pip install -q -r requirements.txt
	touch $@

clean:
	rm -rf .venv server.log
