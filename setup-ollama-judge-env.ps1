chcp 65001
$env:PYTHONUTF8="1"
$env:PYTHONIOENCODING="utf-8"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

$env:OLLAMA_JUDGE_BASE_URL = "http://10.174.192.192:11434"
$env:OLLAMA_JUDGE_NUM_CTX = "4096"
$env:OLLAMA_JUDGE_NUM_PREDICT = "2048"
$env:OLLAMA_JUDGE_KEEP_ALIVE = "0"
$env:OLLAMA_JUDGE_STREAM = "1"
