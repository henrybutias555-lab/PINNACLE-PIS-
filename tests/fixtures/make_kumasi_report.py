"""A second, differently written report: commercial, summary table, other headings."""
import sys

import docx

d = docx.Document()
P = d.add_paragraph
P("VALUATION OF OFFICE PREMISES, PLOT 14 RING ROAD CENTRAL, KUMASI")
t = d.add_table(rows=7, cols=2)
rows = [["Client", "Ecobank Ghana Limited"], ["Property", "Three-storey office block, Plot 14, Adum"],
        ["Purpose", "To advise on value for loan security"], ["Valuation Date", "15 March 2024"],
        ["Digital Address", "AK-039-5028"], ["Tenure", "Leasehold - 50 years from 1 April 2005"],
        ["Market Value", "GHS 6,500,000"]]
for r, vals in zip(t.rows, rows):
    for c, v in zip(r.cells, vals):
        c.text = v
P("A. INSTRUCTIONS")
P("We were instructed by Ecobank Ghana Limited to value the above property.")
P("B. SITUATION")
P("The property is situated in the Adum commercial district of Kumasi, within the Kumasi Metropolitan Assembly. "
  "Access is via the Ring Road Central, a dual carriageway.")
P("C. THE PROPERTY")
P("The property is developed with a three-storey office block with a total lettable area of 1,250 square metres. "
  "The plot measures 0.35 acres. A car park and a generator house are provided at the rear.")
P("D. TENURE")
P("The property is held on a 50-year lease from the Asantehene (stool land) commencing 1st April 2005, and is "
  "vested in Adom Properties Limited. Title is evidenced by an Indenture dated 12th May 2005.")
P("E. OCCUPATION")
P("The building is let to three tenants and is fully occupied.")
P("F. VALUATION APPROACH")
P("The property has been valued using the investment method, capitalising the net rental income at 9%.")
P("G. OPINION OF VALUE")
P("We are of the opinion that the market value of the property is GHS 6,500,000 (Six Million, Five Hundred "
  "Thousand Ghana Cedis), and the forced sale value is GHS 4,550,000.")
P("For: Asante & Partners")
P("Kwame Mensah, FGhIS, MRICS")
d.save(sys.argv[1])
print("saved", sys.argv[1])
