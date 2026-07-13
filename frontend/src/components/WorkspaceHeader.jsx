export default function WorkspaceHeader({ eyebrow, title, description, views = [], activeView, onViewChange, ariaLabel }) {
  return (
    <section className="workspace-header">
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <h2>{title}</h2>
        <p>{description}</p>
      </div>
      {views.length > 0 && <div className="workspace-nav" role="tablist" aria-label={ariaLabel}>
        {views.map((view) => (
          <button
            key={view.id}
            type="button"
            role="tab"
            aria-selected={activeView === view.id}
            className={activeView === view.id ? 'active' : ''}
            onClick={() => onViewChange(view.id)}
          >
            {view.label}
          </button>
        ))}
      </div>}
    </section>
  )
}
