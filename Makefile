# Build the corpus and run the test server.
#
#   make crawl                  # fetch web sources (PDFs optional, see below)
#   make crawl PDFS=~/zid-pdfs  # ...including a folder of PDFs
#   make corpus                 # chunk everything into data/corpus.jsonl
#   make embed                  # needs an OpenAI key
#   make serve                  # http://localhost:8000
#
# PY overrides the interpreter if python3 is not on PATH.

PY   ?= python3
DATA := data
PDFS ?=

$(DATA):
	mkdir -p $(DATA)

help:
	@echo "make crawl [PDFS=dir] | make corpus | make embed | make serve"

$(DATA)/help_center.jsonl: | $(DATA)
	cd ingest && $(PY) fetch_help_center.py --out ../$(DATA)/help_center.jsonl

$(DATA)/marketing.jsonl: | $(DATA)
	cd ingest && $(PY) fetch_marketing.py --out ../$(DATA)/marketing.jsonl

# PDFs are optional: the web sources are the bulk of the corpus, and asking
# for a folder that may not exist should not block a first run.
pdfs: | $(DATA)
	@if [ -n "$(PDFS)" ] && [ -d "$(PDFS)" ]; then \
		cd ingest && $(PY) extract.py "$(PDFS)" --out ../$(DATA)/pdfs.jsonl; \
	else \
		echo "no PDF folder given (PDFS=...), skipping PDFs"; \
	fi

crawl: $(DATA)/help_center.jsonl $(DATA)/marketing.jsonl pdfs

corpus: | $(DATA)
	cd ingest && $(PY) chunk.py $$(ls ../$(DATA)/*.jsonl | grep -v corpus.jsonl) \
		--out ../$(DATA)/corpus.jsonl

embed:
	cd ingest && $(PY) embed.py ../$(DATA)/corpus.jsonl --out ../$(DATA)/vectors.npz

serve:
	cd service && $(PY) serve.py --corpus ../$(DATA)/corpus.jsonl \
		--vectors ../$(DATA)/vectors.npz

.PHONY: help crawl corpus embed serve pdfs
