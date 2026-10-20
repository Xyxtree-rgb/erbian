# 耳辩 · 断案对手 V2 一键部署
# 用法（在 PowerShell 里）：
#   powershell -ExecutionPolicy Bypass -File "D:\耳辩H5\erbian_hackathon\deploy.ps1"
# 加 -InstallKey 会在部署前装一次公钥，之后 AI 侧可免密执行，不再需要你手动传：
#   powershell -ExecutionPolicy Bypass -File "...\deploy.ps1" -InstallKey
# 回滚：
#   powershell -ExecutionPolicy Bypass -File "...\deploy.ps1" -Rollback

param(
  [switch]$InstallKey,
  [switch]$Rollback
)

$ErrorActionPreference = 'Stop'
$Target = 'admin@47.122.109.160'
$Local  = 'D:\耳辩H5\erbian_hackathon'

Write-Host ''
Write-Host '=== 耳辩 · 部署 ===' -ForegroundColor Cyan

if ($InstallKey) {
  Write-Host '[0/3] 安装公钥（只做一次，之后免密）' -ForegroundColor Yellow
  $pub = (Get-Content "$env:USERPROFILE\.ssh\id_ed25519.pub" -Raw).Trim()
  ssh $Target "mkdir -p ~/.ssh; chmod 700 ~/.ssh; grep -q 'workbuddy-erbian-deploy' ~/.ssh/authorized_keys 2>/dev/null || echo '$pub' >> ~/.ssh/authorized_keys; chmod 600 ~/.ssh/authorized_keys; echo KEY_INSTALLED"
}

if ($Rollback) {
  Write-Host '[1/2] 上传回滚脚本' -ForegroundColor Yellow
  scp "$Local\_remote_v2.sh" "${Target}:/tmp/"
  if ($LASTEXITCODE -ne 0) { throw 'scp 失败' }
  Write-Host '[2/2] 执行回滚' -ForegroundColor Yellow
  ssh -t $Target 'bash /tmp/_remote_v2.sh rollback'
  exit $LASTEXITCODE
}

Write-Host '[1/3] 上传 3 个文件（会让你输密码）' -ForegroundColor Yellow
scp "$Local\services\deepseek.py" "$Local\routes\api.py" "$Local\_remote_v2.sh" "${Target}:/tmp/"
if ($LASTEXITCODE -ne 0) { throw 'scp 失败，检查密码 / 网络' }

Write-Host '[2/3] 备份、覆盖、重启、验证（sudo 可能再要一次密码）' -ForegroundColor Yellow
ssh -t $Target 'bash /tmp/_remote_v2.sh'
$code = $LASTEXITCODE

Write-Host '[3/3] 外网复检' -ForegroundColor Yellow
try {
  $r = Invoke-WebRequest -Uri 'http://47.122.109.160/api/health' -TimeoutSec 15 -UseBasicParsing
  Write-Host ("      外网 /api/health -> " + $r.StatusCode) -ForegroundColor Green
} catch {
  Write-Host ("      外网 /api/health 失败: " + $_.Exception.Message) -ForegroundColor Red
}

if ($code -eq 0) {
  Write-Host ''
  Write-Host '部署完成。打开 http://47.122.109.160 打一局，看对手有没有反咬。' -ForegroundColor Green
  Write-Host '要回滚：同一条命令加 -Rollback' -ForegroundColor DarkGray
} else {
  Write-Host ''
  Write-Host '服务器脚本返回非 0，请看上面的输出。回滚：加 -Rollback' -ForegroundColor Red
}
exit $code
