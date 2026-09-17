import time
from rich.console import Console
console = Console()
status = console.status("[bold cyan]Test Spinner[/bold cyan]", spinner="bouncingBar")
status.start()
time.sleep(2)
status.update("[bold cyan]Test Spinner[/bold cyan] [dim][00:02][/dim]")
time.sleep(2)
status.stop()
console.print("Done!")
