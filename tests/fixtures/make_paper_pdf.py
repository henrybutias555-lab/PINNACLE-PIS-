"""A test PDF shaped like a ResearchGate download: a cover page, then a two-paragraph paper."""
import sys

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer


def build(path: str) -> None:
    styles = getSampleStyleSheet()
    body = styles["BodyText"]
    doc = SimpleDocTemplate(path, pagesize=A4, title="Dual rate taxed valuation: A more rational approach",
                            author="A. Mensah; K. Boateng")
    cover = [
        Paragraph("See discussions, stats, and author profiles for this publication at: "
                  "https://www.researchgate.net/publication/235306182", body),
        Paragraph("Dual rate taxed valuation: A more rational approach", styles["Heading2"]),
        Paragraph("Article in Journal of Property Investment &amp; Finance · January 2010", body),
        Paragraph("CITATIONS 12 READS 3,410", body),
        Paragraph("All content following this page was uploaded by A. Mensah on 02 March 2015.", body),
        Paragraph("The user has requested enhancement of the downloaded file.", body),
        PageBreak(),
    ]
    paper = [
        Paragraph("Dual rate taxed valuation: A more rational approach", styles["Title"]),
        Paragraph("Abstract", styles["Heading2"]),
        Paragraph("This paper examines the consequences of sinking fund in the UK. It looks at what is believed to "
                  "be the only rational reason for using the Dual Rate adjusted for tax method variant. It confirms "
                  "that the traditional method is not satisfactory even after the modifications made by the various "
                  "methods mentioned above. The Single Rate Net of Tax approach is proved to meet all expectations "
                  "and can be regarded as a more rational approach to the Dual Rate method.", body),
        Paragraph("Keywords: dual rate, sinking fund, leasehold valuation", body),
        Paragraph("Introduction", styles["Heading2"]),
        Paragraph("Mackmin (2008) provides a detailed review of the dual rate valuation method and suggests that this "
                  "method is defunct because of the flawed reinvestment principle and other shortcomings summarised "
                  "by Baum et al (2006). This paper does not intend to further investigate the problems with the dual "
                  "rate valuation method.", body),
        Spacer(1, 12),
    ]
    doc.build(cover + paper)


if __name__ == "__main__":
    build(sys.argv[1])
    print("saved", sys.argv[1])
