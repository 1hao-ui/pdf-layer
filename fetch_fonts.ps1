# fetch_fonts.ps1 — 拉取四款开源手写字体并用 merge_symbols.py 合并符号字形
# 用法： powershell -ExecutionPolicy Bypass -File .\fetch_fonts.ps1
$ErrorActionPreference = 'Stop'
$dir = Join-Path $PSScriptRoot 'fonts'
New-Item -ItemType Directory -Force -Path $dir | Out-Null

# 名 -> @(CDN 路径, 期望字节数)
$fonts = @(
    @{ f = 'MaShanZheng-Regular.ttf';  u = 'https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/mashanzheng/MaShanZheng-Regular.ttf';  size = 5857936 },
    @{ f = 'LongCang-Regular.ttf';     u = 'https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/longcang/LongCang-Regular.ttf';        size = 5162508 },
    @{ f = 'LiuJianMaoCao-Regular.ttf';u = 'https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/liujianmaocao/LiuJianMaoCao-Regular.ttf'; size = 4951804 },
    @{ f = 'ZhiMangXing-Regular.ttf';  u = 'https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/zhimangxing/ZhiMangXing-Regular.ttf';  size = 4063532 }
)

foreach ($it in $fonts) {
    $p = Join-Path $dir $it.f
    if ((Test-Path $p) -and ((Get-Item $p).Length -eq $it.size)) {
        Write-Host ("skip  {0} (already {1} bytes)" -f $it.f, $it.size); continue
    }
    Write-Host ("get   {0}" -f $it.f)
    # 必须走 CDN：raw.githubusercontent 会超时截断，cmap 校验直接报错
    Invoke-WebRequest -Uri $it.u -OutFile $p -TimeoutSec 180
    $n = (Get-Item $p).Length
    if ($n -ne $it.size) {
        throw ("{0} size mismatch: got {1}, want {2} — 文件被截断，重下" -f $it.f, $n, $it.size)
    }
}

Write-Host 'merge symbols...'
python (Join-Path $PSScriptRoot 'merge_symbols.py')
Write-Host 'done. 产物： fonts/*-merged.ttf'
