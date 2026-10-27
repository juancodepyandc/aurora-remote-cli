use std::{time::{Duration, Instant}, fs};
use ratatui::{layout::{Layout,Constraint}, style::{Style,Color}, text::Line, widgets::{Block,Borders,Paragraph}};
use crossterm::event::{self,Event,KeyCode,KeyModifiers};
fn main() -> std::io::Result<()> {
    let mut terminal=ratatui::init();
    let start=Instant::now(); let mut next=start+Duration::from_secs(3);
    let mut count=0; let mut input=String::new(); let mut dirty=true; let mut times=Vec::new();
    while start.elapsed().as_secs_f64()<9.0 {
        let elapsed=start.elapsed().as_secs_f64(); let active=(3.0..6.0).contains(&elapsed);
        if active && Instant::now()>=next { count+=1; dirty=true; next+=Duration::from_secs_f64(1.0/60.0); }
        if dirty {
            let began=Instant::now();
            terminal.draw(|frame| {
                let areas=Layout::vertical([Constraint::Length(4),Constraint::Min(5),Constraint::Length(3),Constraint::Length(1)]).split(frame.area());
                let glyph=["◌","◔","◑","◕"][count%4];
                let banner=format!("AURORA   {glyph}   REMOTE STUDIO\nMachine demo.local · Session spike · Latence non mesurée");
                frame.render_widget(Paragraph::new(banner).style(Style::default().fg(Color::Rgb(64,224,194))).block(Block::default().borders(Borders::BOTTOM)),areas[0]);
                let lines=(0..count).rev().take(20).collect::<Vec<_>>().into_iter().rev().map(|i|Line::from(format!("{i:03}  Aurora > Vérification du projet, observation reçue."))).collect::<Vec<_>>();
                frame.render_widget(Paragraph::new(lines).block(Block::bordered().title(" Conversation ")),areas[1]);
                frame.render_widget(Paragraph::new(input.as_str()).block(Block::bordered().title(" Votre demande ")),areas[2]);
                frame.render_widget(Paragraph::new(format!("FRAME {count:04}   Entrée : envoyer · Ctrl+Q : quitter")),areas[3]);
            })?;
            times.push(began.elapsed().as_secs_f64()*1000.0); dirty=false;
        }
        if event::poll(if active {next.saturating_duration_since(Instant::now())} else {Duration::from_millis(50)})? {
            match event::read()? {
                Event::Key(key)=>{ if key.code==KeyCode::Char('q') && key.modifiers.contains(KeyModifiers::CONTROL) {break;}
                    match key.code {KeyCode::Char(ch)=>input.push(ch),KeyCode::Backspace=>{input.pop();},_=>{}} dirty=true; },
                Event::Resize(_,_)=>dirty=true,_=>{}
            }
        }
    }
    ratatui::restore();
    if let Ok(path)=std::env::var("SPIKE_METRICS") { fs::write(path,format!("{{\"frame_ms\":{:?},\"frames\":{},\"input\":{:?}}}",times,times.len(),input))?; }
    Ok(())
}
