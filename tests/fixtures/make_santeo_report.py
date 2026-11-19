"""A realistic Ghanaian valuation report (narrative, numbered sections) as .docx."""
import sys

import docx

d = docx.Document()
P = d.add_paragraph
P("PINNACLE ESTATE SURVEYORS")
P("VALUATION REPORT ON A RESIDENTIAL PROPERTY AT SANTEO, EAST LEGON HILLS, ACCRA")
P("Our Ref: PES/VAL/2023/931")
P("1.0 INTRODUCTION")
P("In accordance with instructions received from GCB Bank PLC, Spintex Road Branch, we have inspected the "
  "property described below and now submit our report and valuation for mortgage purposes.")
P("2.0 DATE OF INSPECTION AND VALUATION")
P("The property was inspected on 19th July, 2023 and the valuation is as at 1st August, 2023.")
P("3.0 BASIS OF VALUATION")
P("The basis of valuation is Market Value as defined by the International Valuation Standards (IVS). "
  "We have also provided a Forced Sale Value.")
P("4.0 LOCATION AND NEIGHBOURHOOD")
P("The subject property is located at Santeo, East Legon Hills, within the La Nkwantanang-Madina Municipal "
  "Assembly of the Greater Accra Region. It is about 1.5 kilometres off the Adjiringanor - East Legon Hills road "
  "and is easily accessible via a tarred road. The neighbourhood is predominantly residential with "
  "middle to high income housing. Digital Address: GA-183-8164.")
P("GPS: 5°38'22.6\"N 0°10'03.1\"W")
P("5.0 SITE")
P("The site is rectangular in shape and fairly flat, measuring approximately 0.10 acre (404.69 sq m). "
  "It is fenced with a sandcrete block wall and has a sliding gate.")
P("6.0 TENURE AND TITLE")
P("The property is held under a leasehold interest for a term of 99 years with effect from 1st January, 2015. "
  "The property is registered in the name of Miss Leticia Otoo under Land Title Certificate No. GA 45678 "
  "Vol. 12 Folio 34. We have assumed the title to be good and marketable and free from encumbrances.")
P("7.0 DESCRIPTION OF THE PROPERTY")
P("The subject property comprises a two-storey detached house with a boys quarters and a garage. "
  "The accommodation comprises four bedrooms (all en-suite), a living room, dining area, kitchen, store "
  "room and a visitors' washroom.")
P("8.0 CONSTRUCTION AND FINISHES")
P("The building is constructed of sandcrete block walls rendered and painted, reinforced concrete floors finished "
  "with porcelain tiles, and a long-span aluminium roof on timber trusses.")
P("9.0 SERVICES")
P("The property is connected to ECG electricity and GWCL pipe-borne water. Sewage disposal is by a septic tank.")
P("10.0 FLOOR AREAS")
P("The gross floor area of the main building is 206.55 square metres. The boys quarters measures 24 sq m.")
P("11.0 STATE OF REPAIR")
P("The property is newly built and appears to be in a good structural and tenantable condition.")
P("12.0 PLANNING")
P("The area is zoned for residential use and a building permit was obtained from the Assembly.")
P("13.0 OCCUPATION")
P("The property is currently owner-occupied.")
P("14.0 MARKET COMMENTARY AND COMPARABLE EVIDENCE")
P("Demand for residential property in the area is high. We considered the following transactions:")
t = d.add_table(rows=4, cols=5)
rows = [["No.", "Location", "Description", "Date", "Price (GH¢)"],
        ["1", "East Legon Hills", "4-bedroom house", "March 2023", "2,100,000"],
        ["2", "Santeo", "5-bedroom house", "June 2023", "2,450,000"],
        ["3", "Oyarifa", "4-bedroom house", "January 2023", "1,850,000"]]
for r, vals in zip(t.rows, rows):
    for c, v in zip(r.cells, vals):
        c.text = v
P("15.0 METHOD OF VALUATION")
P("We have adopted the Sales Comparison Approach, supported by the Depreciated Replacement Cost method.")
P("16.0 VALUATION")
P("Having regard to the foregoing, we are of the opinion that the Market Value of the leasehold interest in the "
  "property as at 1st August, 2023 is GH¢2,300,000.00 (Two Million, Three Hundred Thousand Ghana Cedis).")
P("The Forced Sale Value is GH¢1,610,000.00 (One Million, Six Hundred and Ten Thousand Ghana Cedis).")
P("17.0 ASSUMPTIONS AND LIMITING CONDITIONS")
P("We have assumed that the property is not subject to any onerous restrictions. No structural survey was carried "
  "out and services were not tested. This report is for the sole use of the client and should not be relied "
  "upon by any third party. We accept no responsibility for legal matters affecting title.")
P("Yours faithfully,")
P("Audrey Darkwah, BSc, MGhIS")
P("Chartered Valuation Surveyor")
d.save(sys.argv[1])
print("saved", sys.argv[1])
