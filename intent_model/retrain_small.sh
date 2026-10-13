#!/bin/bash
# Retrain e5-small (+ seed 1) after the probs-filename fix, then ensemble + parity + fields parity.
# Status in retrain_small.status; logs in intent_model/*.log
cd /c/Users/HungDao/RAG/ai-engineer || exit 1
export PYTHONIOENCODING=utf-8
PY=.venv/Scripts/python
L=intent_model
: > $L/retrain_small.status
st() { echo "$1 $(date)" >> $L/retrain_small.status; }

$PY $L/finetune.py --model intfloat/multilingual-e5-small --epochs 12 > $L/finetune_small.log 2>&1
st "e5-small done"
$PY $L/finetune.py --model intfloat/multilingual-e5-small --epochs 12 --seed 1 --tag=-s1 > $L/finetune_small_s1.log 2>&1
st "e5-small seed1 done"

$PY $L/ensemble.py probs_lr_bge-m3 probs_ft_multilingual-e5-small probs_ft_multilingual-e5-base probs_ft_multilingual-e5-small-s1 > $L/ensemble_4.log 2>&1
$PY $L/parity.py > $L/parity_run.log 2>&1
$PY $L/fields_parity.py > $L/fields_parity_run.log 2>&1
st "ALL DONE"
