param([ValidateSet('api','router','doctor')][string]$Mode='doctor',[string]$Config='config/settings.json',[string]$Python='python')
$ErrorActionPreference='Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (!(Test-Path $Config)) { throw '请先复制 config/settings.example.json 为 config/settings.json 并填写部署资料。' }
$Command = if ($Mode -eq 'api') {'serve'} else {$Mode}
& $Python -B -m controlplane $Command --config $Config
exit $LASTEXITCODE
