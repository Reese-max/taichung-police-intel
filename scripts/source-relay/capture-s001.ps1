# Taiwan-host capture of the S-001 list page (Windows PowerShell 5.1, ASCII only).
# Run by an SSH forced command on the Taiwan host: it prints one relay JSON
# object and nothing else. It holds no credential and writes only a temp file.
# The URLs, headers and fallback rule mirror online_collect.get_news_listing.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$Urls = @(
    'https://www.police.taichung.gov.tw/ch/home.jsp?id=1&parentpath=0&mcustomize=news_list.jsp',
    'https://www.police.taichung.gov.tw/ch/home.jsp?id=1'
)
# Only a transient failure may fall back to the alternate list; anything else fails closed.
$Transient = @('429', '500', '502', '503', '504')
# curl exit codes of a transient transport failure: resolve, connect, timeout,
# empty reply, send, receive. A TLS or certificate failure is not among them.
$TransientExit = @(6, 7, 28, 52, 55, 56)
$Agent = 'TaichungPoliceIntelSchemaDrift/1.0 (+public-source-monitor)'
$Tmp = [System.IO.Path]::GetTempFileName()
try {
    foreach ($Url in $Urls) {
        $Observed = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss') + '+00:00'
        # A native command's stderr must not become a terminating error here.
        $ErrorActionPreference = 'Continue'
        $Meta = & curl.exe -s --max-time 45 --proto '=https' --max-redirs 0 -A $Agent `
            -H 'Accept-Language: zh-TW,zh;q=0.9' -o $Tmp -w '%{http_code}|%{content_type}' $Url
        $Exit = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'
        if ($Exit -ne 0) {
            if ($TransientExit -contains $Exit) { continue }
            break
        }
        $Status, $ContentType = ([string]$Meta) -split '\|', 2
        if ($Status -ne '200') {
            if ($Transient -contains $Status) { continue }
            break
        }
        $Bytes = [System.IO.File]::ReadAllBytes($Tmp)
        if ($Bytes.Length -eq 0) { break }
        $Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Tmp).Hash.ToLowerInvariant()
        $Relay = [ordered]@{
            schema        = 'govintel-source-relay/v1'
            source_id     = 'S-001'
            requested_url = $Url
            final_url     = $Url
            http_status   = 200
            content_type  = [string]$ContentType
            observed_at   = $Observed
            body_base64   = [System.Convert]::ToBase64String($Bytes)
            body_sha256   = $Hash
            captured_by   = 'taiwan-relay'
        }
        [Console]::Out.Write(($Relay | ConvertTo-Json -Compress))
        exit 0
    }
    [Console]::Error.WriteLine('S-001 capture failed on every catalog URL')
    exit 3
}
finally {
    Remove-Item -LiteralPath $Tmp -Force -ErrorAction SilentlyContinue
}
