# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 W2doingnothing
# Original geometric XL mark. No external fonts or image assets.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$iconTarget = Join-Path (Split-Path -Parent $PSScriptRoot) 'app.ico'
$iconStream = [IO.File]::Create($iconTarget)
$iconWriter = [IO.BinaryWriter]::new($iconStream)
try {
    $iconWriter.Write([uint16]0); $iconWriter.Write([uint16]1); $iconWriter.Write([uint16]1)
    $bitmap = [Drawing.Bitmap]::new(256,256)
    $graphics = [Drawing.Graphics]::FromImage($bitmap)
    try {
        $graphics.SmoothingMode = [Drawing.Drawing2D.SmoothingMode]::AntiAlias
        $graphics.Clear([Drawing.Color]::FromArgb(255,17,24,39))
        $white = [Drawing.Pen]::new([Drawing.Color]::White,18)
        $cyan = [Drawing.Pen]::new([Drawing.Color]::FromArgb(255,34,211,238),18)
        try {
            $graphics.DrawLine($white,48,70,119,177)
            $graphics.DrawLine($white,119,70,48,177)
            $graphics.DrawLine($cyan,157,70,157,177)
            $graphics.DrawLine($cyan,157,177,212,177)
        } finally { $white.Dispose(); $cyan.Dispose() }
        $pngStream = [IO.MemoryStream]::new()
        try {
            $bitmap.Save($pngStream,[Drawing.Imaging.ImageFormat]::Png)
            $bytes = $pngStream.ToArray()
            $iconWriter.Write([byte]0); $iconWriter.Write([byte]0); $iconWriter.Write([byte]0); $iconWriter.Write([byte]0)
            $iconWriter.Write([uint16]1); $iconWriter.Write([uint16]32)
            $iconWriter.Write([uint32]$bytes.Length); $iconWriter.Write([uint32]22)
            $iconWriter.Write($bytes)
        } finally { $pngStream.Dispose() }
    } finally { $graphics.Dispose(); $bitmap.Dispose() }
} finally { $iconWriter.Dispose(); $iconStream.Dispose() }
