// jobradar resume template: one column, standard headings, real text (no images or tables), so applicant tracking
// systems read it the same way people do. Everything shown comes from the JSON passed in as sys.inputs.data, and
// strings from it are always set as plain text, never run as Typst markup.
#let d = json(bytes(sys.inputs.data))
#let st = d.style

#set document(title: d.title, author: d.name)
#set page(paper: st.paper, margin: (x: st.mx * 1cm, y: st.my * 1cm))
#set text(font: st.font, size: st.size * 1pt, ligatures: false, hyphenate: false, lang: "en")
#set par(justify: false, leading: st.leading * 1em, spacing: st.leading * 1em)
#set list(indent: 0.5em, body-indent: 0.45em, spacing: st.leading * 0.95em, marker: [•])

#let lnk(x) = if x.url == "" { x.text } else if st.underline { link(x.url, underline(offset: 1.6pt, stroke: 0.35pt, x.text)) } else { link(x.url, x.text) }
#let links(xs) = xs.map(lnk).join("  |  ")

#let section(title) = {
  v(st.gap * 0.75em)
  block(below: 0.45em, text(size: 1.18em, weight: if st.heading == "bold" { "bold" } else { "regular" },
                             if st.heading == "smallcaps" { smallcaps(title) } else { title }))
  block(above: 0em, below: st.gap * 0.55em, line(length: 100%, stroke: 0.5pt))
}

#let pair(a, b) = grid(columns: (1fr, auto), column-gutter: 1em, a, align(right, b))

#let entry(e) = {
  pair(
    [#strong(e.l1)#if e.l1_links.len() > 0 [#h(0.6em)#links(e.l1_links)]],
    if e.r1_links.len() > 0 { links(e.r1_links) } else { e.r1 },
  )
  if e.l2 != "" or e.r2 != "" {
    v(-0.16em)
    pair(emph(e.l2), emph(e.r2))
  }
  if e.bullets.len() > 0 {
    v(-0.05em)
    list(..e.bullets)
  }
  for role in e.roles {
    v(0.05em)
    pair(emph(role.l), emph(role.r))
    if role.bullets.len() > 0 {
      v(-0.05em)
      list(..role.bullets)
    }
  }
  v(st.gap * 0.18em)
}

#align(center)[
  #text(size: 2.2em, if st.name_smallcaps { smallcaps(d.name) } else { strong(d.name) })
  #v(-0.35em)
  #text(size: 0.92em, d.contact.map(links).join(linebreak()))
]

#for sec in d.sections {
  section(sec.heading)
  if sec.kind == "entries" {
    for e in sec.entries { entry(e) }
  } else if sec.kind == "skills" {
    for row in sec.rows {
      if row.label != "" [#strong(row.label + ":") #row.text] else [#row.text]
      linebreak()
    }
  } else if sec.kind == "text" {
    sec.text
  } else if sec.kind == "bullets" {
    list(..sec.bullets)
  }
}
