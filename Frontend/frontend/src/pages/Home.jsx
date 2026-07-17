import WaterTreatmentArt from '../components/WaterTreatmentArt'

const pillars = [
  { icon: '💧', title: 'Document Generation', desc: 'IODB workbooks, IO lists, and P&ID documentation — automated.',  color: '#0099dd' },
  { icon: '⚙',  title: 'Control Systems',     desc: 'PLC programs, panel schedules, and PCN submittals in minutes.', color: '#00c9a7' },
  { icon: '◈',  title: 'AI Integration',       desc: 'Smarter workflows. Less paperwork. More engineering.',          color: '#a78bfa' },
]

export default function Home() {
  return (
    <div className="home">

      <div className="home-hero">
        <div className="hero-dot-grid" />

        <div className="hero-content">
          <div className="hero-label">Internal Engineering Platform</div>
          <h1 className="hero-title">
            <span className="hero-title-grad">LISA</span>
            <br />v1.0
          </h1>
          <p className="hero-tagline">
            Laminar Integration Systems Agent.<br />
            Water &amp; wastewater, reimagined.
          </p>
        </div>

        <div className="hero-art">
          <WaterTreatmentArt />
        </div>
      </div>

      <div className="pillars">
        {pillars.map(p => (
          <div className="pillar" key={p.title} style={{ '--accent': p.color }}>
            <span className="pillar-icon">{p.icon}</span>
            <div>
              <div className="pillar-title">{p.title}</div>
              <div className="pillar-desc">{p.desc}</div>
            </div>
            <div className="pillar-glow" />
          </div>
        ))}
      </div>

    </div>
  )
}
