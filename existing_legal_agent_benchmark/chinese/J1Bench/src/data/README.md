---
viewer: false
license: apache-2.0
language:
- zh
- en
tags:
- Legal Intelligence
- LLM
- Agent
extra_gated_prompt: You agree to use the dataset to conduct experiments ONLY for research.
extra_gated_fields:
   Affiliation: text
   Country: country
   Specific date: date_picker
   I want to use this dataset for (please answer in detail): text
   I agree to use this dataset for non-commercial use **ONLY**: checkbox
---

# Data Statistics of J1-Eval
| Level   | Level I       |             | Level II                          |              | Level III     |                          |
|---------|---------------|-------------|-----------------------------------|--------------|---------------|--------------------------|
| Env.    | KQ            | LC          | CD                                | DD           | CI            | CR                       |
|         | 98            | 62          | 93                                | 93           | 93            | 69                       |
| Source  | Legal Article |             | Civil Judgment Document           |              |               | Criminal Judgment Document |
|         | 160           |             | 93                                |              |               | 69                       |


# Citation
We are grateful if you find our work useful, and please cite our paper as follows:
```
@article{jia2025readyjuristonebenchmarking,
  author    = {Zheng Jia and Shengbin Yue and Wei Chen and Siyuan Wang and Yidong Liu and Yun Song and Zhongyu Wei},
  title     = {Ready Jurist One: Benchmarking Language Agents for Legal Intelligence in Dynamic Environments},
  year      = {2025},
 journal    = {arXiv preprint arXiv:2507.04037},
 url        = {https://arxiv.org/abs/2507.04037}
}