# Build the corpus and run the test server.
#   make corpus PDFS=/path/to/pdfs   # crawl + chunk (no API key needed)
#   make embed                       # needs OPENAI_API_KEY
#   make serve                       # needs OPENAI_API_KEY

DATA := data
PDFS ?= ./pdfs

$(DATA):
	mkdir -p $(DATA)

crawl: | $(DATA)
	cd ingest && python3 fetch_help_center.py --out ../$(DATA)/help_center.jsonl
	cd ingest && python3 fetch_marketing.py --out ../$(DATA)/marketing.jsonl
	cd ingest && python3 extract.py $(PDFS) --out ../$(DATA)/pdfs.jsonl

corpus: | $(DATA)
	cd ingest && python3 chunk.py ../$(DATA)/help_center.jsonl \
		../$(DATA)/marketing.jsonl ../$(DATA)/pdfs.jsonl \
		--out ../$(DATA)/corpus.jsonl

embed:
	cd ingest && python3 embed.py ../$(DATA)/corpus.jsonl --out ../$(DATA)/vectors.npz

serve:
	cd service && python3 serve.py --corpus ../$(DATA)/corpus.jsonl \
		--vectors ../$(DATA)/vectors.npz

.PHONY: crawl corpus embed serve
