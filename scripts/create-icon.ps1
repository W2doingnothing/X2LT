# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 W2doingnothing
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$defaultIcon = Join-Path $projectRoot 'frontend/public/app-icons/gray-pixel.ico'
$iconTarget = Join-Path $projectRoot 'app.ico'
Copy-Item -LiteralPath $defaultIcon -Destination $iconTarget -Force
