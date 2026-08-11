# LEGALWORLD Dataset

LEGALWORLD is a lifecycle interactive environment tailored for legal agents, modeled based on China’s civil litigation procedures\. It covers successive procedural stages including legal consultation, document drafting, first\-instance trial, appeal adjudication, appeal drafting and second\-instance trial\. This dataset provides case data and legal provision retrieval data to support research on the LEGALWORLD environment and relevant legal agents\.

## Data File Description

- `Full_version_raw.json`, `medium_version_raw.json`, `light_version_raw.json`: The three raw versions contain original case data distinguished only by data scale, applicable to custom information extraction, data cleansing, modeling and experimental construction\.

- `light_case_dataset.json`: Processed lightweight case dataset with essential information extraction and structuring finished, which can be directly adopted for LEGALWORLD\-related tasks and downstream experiments\.

- `law_metadata.jsonl`: Raw data for legal provision retrieval\. Users can generate embeddings for legal provision texts independently, and build legal provision retrieval tools combined with vector databases or retrieval modules\.

## Relevant Links

- Project Page: https://chidaic\.github\.io/Legal\-world/

- Paper: https://arxiv\.org/abs/2606\.18728

- Demo: http://www\.fudan\-disc\.com/legalworld/

LEGALWORLD defines civil litigation as an interconnected lifecycle process instead of a set of isolated tasks\. The case and legal provision resources in the dataset support research on legal agents’ capabilities in multi\-stage litigation workflows, covering legal consultation, legal reasoning, legal document generation, courtroom interaction and legal provision retrieval\.


