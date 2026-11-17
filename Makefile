# make run                      start the transcript server + a readable recap every 30 s (Claude API)
# make cc                       same, but through Claude Code on your Claude plan
# make live                     line-by-line live translation instead (add BACKEND=claude-code for your plan)
# make run TO=Spanish NOTES=notes.txt RECAP=120 MODEL=claude-sonnet-5-5
# make cc REPO=path/to/docs     let the recap look up names and terms in a docs/code folder
# make server / make translate  run either half on its own (e.g. `make translate RECAP=0` for live next to a recap)

PY         := .venv/bin/python
BACKEND    ?= api
MODEL      ?= claude-opus-5-5
TRANSCRIPT ?= transcript.txt
HOST       ?= 127.0.0.1
# Left empty, these default by mode in translate.py: a recap every 30 s at medium
# effort into recap.txt, or with RECAP=0, live at low effort into translation.txt.
RECAP      ?=
EFFORT     ?=
OUT        ?=
NOTES      ?=
TO         ?=
REPO       ?=

SERVER_ARGS    := --out $(TRANSCRIPT) --host $(HOST)
TRANSLATE_ARGS := --backend $(BACKEND) --model $(MODEL) --transcript $(TRANSCRIPT) \
                  $(if $(RECAP),--recap $(RECAP)) $(if $(EFFORT),--effort $(EFFORT)) \
                  $(if $(OUT),--out $(OUT)) $(if $(NOTES),--notes $(NOTES)) \
                  $(if $(REPO),--repo $(REPO)) $(if $(TO),--to "$(TO)")

.PHONY: run cc live server translate install clean

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

live:
	@$(MAKE) --no-print-directory run RECAP=0

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
