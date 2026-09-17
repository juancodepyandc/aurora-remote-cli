import time
from rich.console import Console
from rich.status import Status

console = Console()
status_spinner = console.status(f"[bold cyan]En cours :[/bold cyan] [white]Réflexion[/white]", spinner="bouncingBar")
status_spinner.start()

time.sleep(3)
status_spinner.stop()
console.print("Done")
