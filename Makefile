# Build the corpus and run the test server.
#
#   make crawl                       # fetch web sources
#   make crawl PDFS=~/Downloads/pdfs # ...including a folder of PDFs
#   make corpus                      # chunk everything into data/corpus.jsonl
#   make serve                       # http://localhost:8000, index built in-page
#   make embed                       # optional: build the index from the CLI
#
# PY overrides the interpreter if python3 is not on PATH.

PY   ?= python3
DATA := data
PDFS ?=
# A tilde inside double quotes is literal to the shell, so "~/zid-pdfs" never
# matches a real directory however carefully it was typed. Expand it here.
PDFS_DIR := $(patsubst ~/%,$(HOME)/%,$(PDFS))

$(DATA):
	mkdir -p $(DATA)

help:
	@echo "make crawl [PDFS=dir] | make corpus | make serve | make embed"

# Crawl targets are phony, not file targets. As file targets, make treats an
# existing data/help_center.jsonl as up to date and does nothing — "make
# crawl" then prints a summary having fetched not one page, which is how a
# five-page corpus survived three attempts to rebuild it. No dependency can
# express "the website changed", so asking for a crawl must perform one.
help-center: | $(DATA)
	cd ingest && $(PY) fetch_help_center.py --out ../$(DATA)/help_center.jsonl

marketing: | $(DATA)
	cd ingest && $(PY) fetch_marketing.py --out ../$(DATA)/marketing.jsonl

# A PDFS path that does not exist is a typo, not a decision to skip PDFs.
pdfs: | $(DATA)
	@if [ -z "$(PDFS_DIR)" ]; then \
		echo "no PDF folder given (PDFS=...), skipping PDFs"; \
	elif [ ! -d "$(PDFS_DIR)" ]; then \
		echo "ERROR: PDFS=$(PDFS) resolved to $(PDFS_DIR), which is not a directory."; \
		exit 1; \
	else \
		cd ingest && $(PY) extract.py "$(PDFS_DIR)" --out ../$(DATA)/pdfs.jsonl; \
	fi

# Hand-maintained answers that exist on no page. Runs first: it is the only
# source that can be wrong because someone forgot to update it, so it should
# be the first thing printed.
facts: | $(DATA)
	cd ingest && $(PY) fetch_facts.py ../facts --out ../$(DATA)/facts.jsonl

crawl: facts help-center marketing pdfs

corpus: | $(DATA)
	cd ingest && $(PY) chunk.py $$(ls ../$(DATA)/*.jsonl | grep -v corpus.jsonl) \
		--out ../$(DATA)/corpus.jsonl

embed:
	cd ingest && $(PY) embed.py ../$(DATA)/corpus.jsonl --out ../$(DATA)/vectors.npz

serve:
	cd service && $(PY) serve.py --corpus ../$(DATA)/corpus.jsonl \
		--vectors ../$(DATA)/vectors.npz

.PHONY: help crawl corpus embed serve pdfs help-center marketing facts
