' Запуск Rieltor Dashboard без вікна консолі.
'
' Лежить у корені проєкту (поряд із launch_dashboard.py). Запускає лаунчер через
' .venv\Scripts\python.exe прихованим вікном — це той самий перевірено робочий
' шлях, що й "python.exe launch_dashboard.py", але без чорного вікна консолі та
' без ненадійного pythonw.exe. Шлях до проєкту визначається відносно цього файлу,
' тож працює на будь-якому ПК.
'
' Використання: подвійний клік по файлу (або по ярлику на нього).

Option Explicit
Dim fso, sh, proj, py, script
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")

proj = fso.GetParentFolderName(WScript.ScriptFullName)
py = proj & "\.venv\Scripts\python.exe"
script = proj & "\launch_dashboard.py"

sh.CurrentDirectory = proj
' 0 = приховане вікно (ховаємо консоль python.exe); False = не чекати завершення.
' Вікно-керування «Rieltor Dashboard» (tkinter) усе одно зʼявиться окремо.
sh.Run """" & py & """ """ & script & """", 0, False
