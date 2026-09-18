import re
with open("/home/juan/aurora-remote-cli/aurora_cli/interactive.py", "r", encoding="utf-8") as f:
    content = f.read()

# Remplacer le J.O.B.I.A ⚡ > par un prompt Matrix
content = content.replace('console.print("J.O.B.I.A ⚡ > ", end="", flush=True)', 
                          'console.print("[bold green]NEXUS[/bold green] [dim cyan]>[/dim cyan] ", end="", flush=True)')

# Remplacer "Aurora ⚡ > "
content = content.replace('console.print("Aurora ⚡ > ", end="", flush=True)', 
                          'console.print("[bold magenta]AURORA[/bold magenta] [dim]>[/dim] ", end="", flush=True)')

# Amélioration du Live Swarm
old_swarm = """                with Live(auto_refresh=True, console=console) as live:
                    while not done_event.is_set():
                        elapsed = time.time() - start_time
                        
                        # Déterminer l'étape actuelle
                        current_step_text = steps[-1][1]
                        for i in range(len(steps)):
                            if elapsed < steps[i][0]:
                                current_step_text = steps[i-1][1] if i > 0 else steps[0][1]
                                break
                                
                        spinner = Spinner("dots", text=Text(f"[{elapsed:.1f}s] {current_step_text}", style="cyan"))
                        panel = Panel(spinner, border_style="magenta", title="[bold cyan]🧠 J.O.B.I.A Engine en cours[/bold cyan]", expand=False)
                        live.update(panel)
                        time.sleep(0.1)"""

new_swarm = """                from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
                
                with Live(auto_refresh=True, console=console) as live:
                    while not done_event.is_set():
                        elapsed = time.time() - start_time
                        
                        # Smooth transition logic
                        current_idx = len(steps) - 1
                        for i in range(len(steps)):
                            if elapsed < steps[i][0]:
                                current_idx = i - 1 if i > 0 else 0
                                break
                                
                        step_text = steps[current_idx][1]
                        progress_pct = min(100, int((elapsed / 15.0) * 100))
                        
                        # Build a Pro-Level Multi-Element Layout
                        grid = Table.grid(expand=True)
                        grid.add_column()
                        grid.add_row(Spinner("bouncingBar", text=Text(f" {step_text}", style="bold cyan")))
                        grid.add_row(f"[dim magenta]Phase {current_idx+1}/{len(steps)} | Time: {elapsed:.1f}s[/dim magenta]")
                        
                        # Progress bar simulation
                        bar = "[" + "="*(progress_pct//5) + ">" + "."*(20 - progress_pct//5) + "]"
                        grid.add_row(f"[bold blue]{bar}[/bold blue] {progress_pct}%")

                        panel = Panel(
                            grid, 
                            border_style="magenta", 
                            title="[bold cyan]🧠 J.O.B.I.A COGNITIVE ENGINE[/bold cyan]", 
                            box=box.HEAVY, 
                            padding=(1, 2)
                        )
                        live.update(panel)
                        time.sleep(0.05)"""

content = content.replace(old_swarm, new_swarm)

# Améliorer l'affichage final J.O.B.I.A
old_result = """                with Live(auto_refresh=False, console=console) as live:
                    for line in lines:
                        displayed_text += line + "\n"
                        # Utilisation de justify="left" et d'un code_theme pour sublimer les maths et le code
                        md = Markdown(displayed_text, justify="left", code_theme="monokai")
                        panel = Panel(md, border_style="cyan", title="[bold magenta]Synthèse J.O.B.I.A[/bold magenta]", expand=False, padding=(1, 2))
                        live.update(panel, refresh=True)
                        time.sleep(0.03)"""

new_result = """                # Use the ultra-smooth typing effect from display
                import aurora_cli.display as d
                d.typing_effect(task_result["text"], speed=0.005, title="🧠 Synthèse J.O.B.I.A")"""

content = content.replace(old_result, new_result)

with open("/home/juan/aurora-remote-cli/aurora_cli/interactive.py", "w", encoding="utf-8") as f:
    f.write(content)

