# Hand-testing MAESTRO.exe on Windows

About 20 minutes. Do this once on a real Windows 10 or 11 PC (64-bit) before
demonstrating the Windows build. The GitHub build already checks the files in
the bundle; this checks the things only a person at a real desktop can: the
window opens, the app works, and closing it really ends it.

Nothing here uses your Google account or changes your files. Every task below
is read-only, refused, or denied before it runs.

## What you need

- Windows 10 or 11, 64-bit.
- **Microsoft Edge WebView2 Runtime.** Already present on Windows 11 and on most
  updated Windows 10 PCs. If the MAESTRO window opens blank, install it from
  <https://developer.microsoft.com/microsoft-edge/webview2/> (Evergreen
  Standalone Installer, x64).
- Internet only for the optional voice-input test (it downloads the speech model
  once, about 150 MB).

## 1. Download the build

1. Open the repository on GitHub → **Actions** → **Windows package (manual)**.
2. Open the newest run with a green tick. Under **Artifacts**, download
   **MAESTRO-windows-portable**.
3. GitHub wraps it in one more zip. Extract that; you get two files:
   `MAESTRO-windows-portable.zip` and `MAESTRO-windows-portable.zip.sha256`.
4. Check the download is intact. In PowerShell, in that folder:

   ```powershell
   (Get-FileHash .\MAESTRO-windows-portable.zip -Algorithm SHA256).Hash.ToLower()
   Get-Content .\MAESTRO-windows-portable.zip.sha256
   ```

   The first hash must equal the first word of the `.sha256` file.

## 2. Unzip the whole folder

1. Extract `MAESTRO-windows-portable.zip` to a simple path, for example
   `C:\MAESTRO-test\`. You get `C:\MAESTRO-test\MAESTRO\` containing
   `MAESTRO.exe` and an `_internal` folder. **Keep them together**: the exe does
   not work on its own.
2. Files downloaded from the internet are marked as blocked. Unblock them once:

   ```powershell
   Get-ChildItem -Recurse C:\MAESTRO-test\MAESTRO | Unblock-File
   ```

## 3. Run the automated launch checks

Get `scripts\test_windows_package.ps1` from the repository (clone it, or download
that one file), then:

```powershell
powershell -ExecutionPolicy Bypass -File .\test_windows_package.ps1 -Exe C:\MAESTRO-test\MAESTRO\MAESTRO.exe
```

It starts MAESTRO, checks it listens on 127.0.0.1 only, that the window titled
"MAESTRO" opens, that the microphone is not started and no Google token is
created, then closes the window and checks nothing is left running or listening.
It must end with **All automated checks passed.** Keep its output.

## 4. Click through the app

Double-click `MAESTRO.exe`. Tick each row, or write what happened instead.

| # | Do this | Expected | OK? |
|---|---|---|---|
| 1 | First launch | Windows SmartScreen may say "Windows protected your PC" (the build is not code-signed). Click **More info → Run anyway**. The MAESTRO window opens. **No black console window** appears. | |
| 2 | Look at the top bar | "MAESTRO ready" and "Audit log intact" | |
| 3 | Assistant: type `what time is it`, **Preview plan** | A plan with risk **R0**; nothing has run yet | |
| 4 | **Approve and run** | Completes and shows the time | |
| 5 | Type `delete everything in Documents permanently`, **Preview plan** | **Refused**; nothing runs | |
| 6 | Type `move my files`, **Preview plan** | MAESTRO asks which folder. Click **Cancel** | |
| 7 | Type `move the pdfs from Downloads to Documents/Invoices`, **Preview plan** | Risk **R2**, needs approval, with a dry-run file count. Click **Deny**. Downloads is unchanged | |
| 8 | **Activity** page | The tasks above are listed; the audit log says **intact** | |
| 9 | **Connections** page | Google shows **Setup required** (no client file on this PC). Connect is off | |
| 10 | **Voice** page → Spoken replies → **On** → **Test voice** | You hear the Windows voice say the sample sentence. Then switch it **Off** | |
| 11 | *Optional.* Voice → **Start recording**, say "what time is it", **Stop recording** | Windows may ask for microphone permission. The first time, the speech model downloads (about 150 MB, one-off). The words appear in the text box | |
| 12 | *Optional.* **Use in Assistant** | The text is copied into the Assistant box. **Nothing runs** until you click Preview plan | |
| 13 | Narrow the window to about half the screen | The sidebar turns into a top bar; nothing overlaps | |
| 14 | Close the window (X) | The app closes completely. In Task Manager there is no `MAESTRO.exe` left | |
| 15 | Start `MAESTRO.exe` again | It opens again; Activity still shows the earlier tasks | |

MAESTRO keeps its data in `%USERPROFILE%\.maestro` and
`%USERPROFILE%\maestro_workspace`, never inside the app folder. To reset the
test, close MAESTRO and delete those two folders.

## 5. Report back

Send:

- the output of step 3;
- the table from step 4 with any row that did not match, and a screenshot of it;
- the Windows version (Settings → System → About).

## If something goes wrong

| What you see | Try |
|---|---|
| Blank white window | Install the WebView2 Runtime (link above), then start MAESTRO again. |
| "MAESTRO could not start" dialog | Note what you did just before it, and send the step-3 output. |
| The exe disappears or is quarantined | Your antivirus flagged the unsigned build. Restore it and allow `C:\MAESTRO-test\MAESTRO\`. |
| No voice in row 10 | Check the volume and that a Windows voice is installed (Settings → Time & language → Speech). |
| Microphone does nothing in row 11 | Settings → Privacy & security → Microphone: turn on microphone access and "Let desktop apps access your microphone". |
