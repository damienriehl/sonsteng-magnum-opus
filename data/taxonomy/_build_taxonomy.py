#!/usr/bin/env python3
"""Generator for the Sonsteng skills/tasks taxonomy + FOLIO crosswalk.

Builds data/taxonomy/{skills.json,tasks.json,folio-crosswalk.json} from a single
source of truth, assigns deterministic IDs, and validates against the schemas.

Every FOLIO IRI below was verified live via the folio MCP (search_concepts /
get_children / get_parents returned the label + definition recorded here) on
2026-07-17. This file is the offline crosswalk source of truth thereafter.
"""
import json, os, sys

BASE = "https://sonsteng.damienriehl.com/spine"
SV = "1.0.0"
HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# Verified FOLIO concept registry: iri -> (label as retrieved, top-level branch)
# ---------------------------------------------------------------------------
C = {
    # --- Services branch (verified via get_children of the 5 Services families) ---
    "RNlaIvo9pK9MgOcwKug5on": ("Case Assessment, Development, Administration", "services"),
    "R8Gc3Ce7YAbKZLOUs4pCQuA": ("Opinion Memo Practice", "services"),
    "R9p98NogvwJk1MnJgYzuiZd": ("Advisory Service", "services"),
    "RDa0Qu7gpJEIhpO0REOfc1p": ("Settlement, Demand, and Collection Practice", "services"),
    "R79LjWBKHCdmQKEW6zPY8Co": ("Litigation Practice", "services"),
    "RBgzWJQz52CrObfGXDKH1t7": ("Trial Court Practice", "services"),
    "RBKT1I6J8ce2Gs6xwwFcUTr": ("Appellate Practice", "services"),
    "RXO2u2WBkUUcEG1jczLIcA": ("Arbitration Practice", "services"),
    "R9zgcjBBIdWMnwIR8nqsoS8": ("Mediation Practice", "services"),
    "R9hR0S1zExtgPQTzV0LeyD3": ("Discovery Practice", "services"),
    "RDzSLCTTihtIsY2Vostr1Fn": ("Discovery Motion Practice", "services"),
    "RBNIcoXeUcBXVeFvlXkbGaS": ("Written Discovery Practice", "services"),
    "RDrCoGiMIXR6TcVXppICUT5": ("Deposition Practice", "services"),
    "R9JHul9FKmkFasxXopMmrpq": ("Affidavit / Declaration Practice", "services"),
    "RCijZPAaPw0lgYwtl8qWG67": ("Purchase and Sale Practice", "services"),
    "R9bCaZjWhV1f2PgQiwzAkDm": ("Estate Management Practice", "services"),
    "RVZJBUCRB7cgWN1CIbeJJq": ("Fact Investigation and Development", "services"),
    # --- Business of Law branch (verified via get_children of Business of Law) ---
    "RBJy3KoNBYSWUf4xdARMhYt": ("Practice Management", "business_of_law"),
    "RgV0GDy1p5PQCXleadL2hV": ("Billing Management", "business_of_law"),
    "R9RWwdyMp12ldMe0u2EZjhO": ("Pricing Management", "business_of_law"),
    "RW7P6EPg4NMe8dhyO52jzl": ("Budgeting Management", "business_of_law"),
    "R81n0uoiTFQMwwdzAskqkZD": ("Business Development Management", "business_of_law"),
    "RDpaN9hLN8jGKrKBnQ2kPbN": ("Conflicts Management", "business_of_law"),
    "R9PLqqxNIpwbQxLWPoLvR5n": ("CRM Management", "business_of_law"),
    "RDCyhgZrQPH8YgEp0C1Jtvb": ("Matter Management Management", "business_of_law"),
    "Ry2zpiHIeLctXWkmz2CO6C": ("New Business Intake Management", "business_of_law"),
    "R8MT8gA5gTVF6rbGoWbxYJw": ("Pitches and Proposals Management", "business_of_law"),
    "R9ScBHXlUfa31SlBnKmDarJ": ("Project Management", "business_of_law"),
    "R9MD9rwRADsqy2eadoz5R8L": ("Records Management", "business_of_law"),
    "Ru8IZ0c4FPR4BSj4MyRQT3": ("Risk Management", "business_of_law"),
    "RDDsp1sHEeFoxTQnJeEyPPh": ("Timekeeping Management", "business_of_law"),
    "R8Ol92hxELxA00tCrZWrQDK": ("Work Allocation Management", "business_of_law"),
    "R97IEI6qOls8PLTHVBTaE0w": ("Information Governance Management", "business_of_law"),
    # --- Other branches (verified via get_parents) ---
    "R7TB5AJsngthjmL6KsEht3G": ("Legal Research", "legal_use_cases"),
    "R83QdGrsILzbGVfk0bqF1kw": ("Interview", "discovery_events"),
    "RBwgCSrndWQriCjEO63iWbx": ("Engagement Letter", "document_artifacts"),
    "RPzuFFXebWVvkhzE9KZ304": ("Letter Communication", "communication"),
    "R77n3Z14OcgllZTAx8sw9xZ": ("Legal Argument", "document_artifacts"),
}

# matter-group helpers for exercise_refs -------------------------------------
def ex(*ids): return [f"{m}.ex" for m in ids]
ARB=("m01","m11"); DISC=("m02","m12"); TORT=("m03","m13"); RE=("m04","m14")
DWI=("m05","m15"); NC=("m06","m16"); UCC=("m07","m17"); JUV=("m08","m18")
DISS=("m09","m19"); PROB=("m10","m20")
ALL20=tuple(f"m{i:02d}" for i in range(1,21))

# ---------------------------------------------------------------------------
# SKILLS  (26 surveyed + 5 AI-era extensions)
# folio: ("Rid","exact|near|parent") or None (=> no_folio_equivalent, needs note)
# ---------------------------------------------------------------------------
SKILLS = [
 # id, name, alt_name, category, extension, folio, survey, note(for no_folio)
 ("SK-LP-01","Ability to diagnose and plan solutions for legal problems",None,"legal_practice",False,
   ("RNlaIvo9pK9MgOcwKug5on","near"),{"importance":96.1},None),
 ("SK-LP-02","Ability in legal analysis and reasoning",None,"legal_practice",False,None,{"importance":96.8,"preparedness":84.3},
   "FOLIO Services models delivered service types, not the cross-cutting cognitive skill of legal analysis and reasoning; the skill's concrete tasks map to Opinion Memo Practice and Legal Argument."),
 ("SK-LP-03","Knowledge of substantive law",None,"legal_practice",False,None,None,
   "Substantive-law knowledge spans FOLIO's areas_of_law branch generally rather than any single service concept; the skill's tasks map to specific practice and research concepts."),
 ("SK-LP-04","Knowledge of procedural law",None,"legal_practice",False,None,None,
   "Procedural-law knowledge is embodied across Litigation, Trial Court, and Appellate Practice; no single FOLIO concept captures the knowledge competency itself."),
 ("SK-LP-05","Library legal research","Skills to conduct legal library research","legal_practice",False,
   ("R7TB5AJsngthjmL6KsEht3G","near"),{"importance":28.5},None),
 ("SK-LP-06","Computer legal research","Knowledge of computer legal research","legal_practice",False,
   ("R7TB5AJsngthjmL6KsEht3G","near"),{"importance":87.1,"preparedness":81.7},None),
 ("SK-LP-07","Fact gathering",None,"legal_practice",False,
   ("RVZJBUCRB7cgWN1CIbeJJq","near"),None,None),
 ("SK-LP-08","Oral communication",None,"legal_practice",False,None,{"importance":96.0,"preparedness":72.7},
   "Oral communication is a generic delivery skill with no FOLIO service equivalent; its tasks map to Trial Court Practice, Advisory Service, and Settlement/Demand/Collection Practice."),
 ("SK-LP-09","Written communication",None,"legal_practice",False,None,{"importance":97.4,"preparedness":78.6},
   "Written communication is a generic skill; concrete tasks map to Letter Communication, Opinion Memo Practice, and Appellate Practice."),
 ("SK-LP-10","Counseling",None,"legal_practice",False,
   ("R9p98NogvwJk1MnJgYzuiZd","near"),{"importance":86.4,"preparedness":39.1},None),
 ("SK-LP-11","Instilling others' confidence in you","The ability to instill others' confidence in their work","legal_practice",False,None,{"importance":89.0,"preparedness":42.0},
   "Instilling confidence is an interpersonal skill with no FOLIO equivalent; the nearest adjacent firm concept is CRM Management."),
 ("SK-LP-12","Ability to obtain and keep clients",None,"legal_practice",False,
   ("R81n0uoiTFQMwwdzAskqkZD","near"),{"importance":80.4,"preparedness":10.0},None),
 ("SK-LP-13","Negotiation",None,"legal_practice",False,
   ("RDa0Qu7gpJEIhpO0REOfc1p","near"),{"importance":86.2,"preparedness":43.6},None),
 ("SK-LP-14","Litigation","Understanding and conducting litigation","legal_practice",False,
   ("R79LjWBKHCdmQKEW6zPY8Co","near"),{"importance":71.5,"preparedness":28.7},None),
 ("SK-LP-15","Organization and management of legal work",None,"legal_practice",False,
   ("RDCyhgZrQPH8YgEp0C1Jtvb","near"),{"importance":85.2,"preparedness":30.7},None),
 ("SK-LP-16","Sensitivity to professional and ethical concerns",None,"legal_practice",False,None,None,
   "FOLIO has no legal-ethics / professional-responsibility service concept; the skill's tasks map to Conflicts Management, Risk Management, and Billing Management (trust duties)."),
 ("SK-LP-17","Drafting legal documents","Ability to draft legal documents","legal_practice",False,None,{"importance":91.0,"preparedness":45.0},
   "FOLIO has no generic legal-drafting concept; drafting is embodied across Purchase and Sale, Estate Management, Engagement Letter, and litigation-document concepts to which the tasks map."),
 # --- 9 Practice Management ---
 ("SK-PM-01","Fee arrangements, pricing, billing",None,"practice_management",False,
   ("RgV0GDy1p5PQCXleadL2hV","near"),{"preparedness":9.0},None),
 ("SK-PM-02","Human resources, hiring, support staff",None,"practice_management",False,
   ("RBJy3KoNBYSWUf4xdARMhYt","parent"),{"preparedness":8.9},None),
 ("SK-PM-03","Capitalization, investment",None,"practice_management",False,None,{"preparedness":4.0},
   "FOLIO's Business of Law branch has no firm-capitalization / investment concept; the nearest adjacent concept is Budgeting Management."),
 ("SK-PM-04","Project and time management, efficiency",None,"practice_management",False,
   ("R9ScBHXlUfa31SlBnKmDarJ","near"),{"preparedness":40.3},None),
 ("SK-PM-05","Planning, resource allocation, budgeting",None,"practice_management",False,
   ("RW7P6EPg4NMe8dhyO52jzl","near"),{"preparedness":13.7},None),
 ("SK-PM-06","Marketing, client development",None,"practice_management",False,
   ("R81n0uoiTFQMwwdzAskqkZD","near"),{"preparedness":10.2},None),
 ("SK-PM-07","Technology, computers, communications",None,"practice_management",False,None,{"preparedness":53.4},
   "FOLIO's technology concepts (Records, Information Governance, Email Management) are narrow tooling categories, not the firm-IT competency; mapped no-equivalent at the skill level, with tasks mapping to those tooling concepts."),
 ("SK-PM-08","Governance, decision-making, long-range strategic planning",None,"practice_management",False,
   ("RBJy3KoNBYSWUf4xdARMhYt","parent"),{"preparedness":13.4},None),
 ("SK-PM-09","Interpersonal communications, staff relations",None,"practice_management",False,
   ("RBJy3KoNBYSWUf4xdARMhYt","parent"),{"preparedness":39.6},None),
 # --- AI-era extension set (extension:true, excluded from the surveyed 26) ---
 ("SK-LP-18","Prompt formulation and iteration",None,"legal_practice",True,None,None,
   "AI-era competency not yet modeled in FOLIO; candidate for an upstream contribution to the FOLIO Services / Legal Use Cases branches."),
 ("SK-LP-19","AI-output verification and citation checking",None,"legal_practice",True,None,None,
   "AI-era competency not yet modeled in FOLIO; adjacent to Legal Research (cite validation) but has no dedicated concept; candidate for upstream contribution."),
 ("SK-LP-20","Centaur workflow judgment",None,"legal_practice",True,None,None,
   "AI-era competency (deciding what to delegate to AI vs. retain for human judgment) not yet modeled in FOLIO; candidate for upstream contribution."),
 ("SK-LP-21","Confidentiality and privilege protection in AI-assisted work",None,"legal_practice",True,None,None,
   "AI-era competency not yet modeled in FOLIO; adjacent to Risk Management and Information Governance Management but has no dedicated concept; candidate for upstream contribution."),
 ("SK-PM-10","Data and matter hygiene with AI tools",None,"practice_management",True,None,None,
   "AI-era practice-management competency not yet modeled in FOLIO; adjacent to Records Management and Information Governance Management; candidate for upstream contribution."),
]

# ---------------------------------------------------------------------------
# TASKS: skill_id -> list of primary tasks.
# task = (name, description, bloom, module, [subtask(name,desc)...], folio, refs)
#   folio = ("Rid","exact|near|parent") or ("NONE","note")
# ---------------------------------------------------------------------------
def st(*pairs): return list(pairs)
NONE = lambda note: ("NONE", note)

TASKS = {
 "SK-LP-01": [
  ("Conduct a preliminary case analysis",
   "Analyze a new matter to identify governing law and the relative strengths and weaknesses of each side.",
   "analysis","M2",
   st(("Identify the governing law","Determine which statutes, rules, and cases control the matter."),
      ("Assess strengths and weaknesses","Weigh the strong and weak points of both sides."),
      ("Frame the legal issues","State the dispositive issues the matter presents.")),
   ("RNlaIvo9pK9MgOcwKug5on","near"), ex("m01","m03","m05","m06")),
  ("Develop a case theory and strategy",
   "Synthesize the facts and law into a coherent theory of the case and a plan to prevail.",
   "synthesis","M2",
   st(("Articulate theory and themes","State the persuasive theory and supporting themes."),
      ("Identify elements to prevail","List the elements that must be proven or defeated."),
      ("Map liabilities and remedies","Identify exposure and the remedies available.")),
   ("RNlaIvo9pK9MgOcwKug5on","near"), ex("m03","m06","m05","m14")),
  ("Create a strategic plan of action",
   "Translate the case theory into a sequenced plan with objectives, steps, and deadlines.",
   "synthesis","M2",
   st(("Define client objectives","Capture what the client wants to achieve."),
      ("Sequence steps and deadlines","Order the tasks and key dates."),
      ("Select the forum and approach","Choose litigation, negotiation, or transaction path.")),
   ("RNlaIvo9pK9MgOcwKug5on","near"), ex("m04","m01","m09")),
  ("Diagnose the client's legal problem at intake",
   "Classify the client's problem into the correct area(s) of law and spot threshold issues.",
   "comprehension","M1",
   st(("Classify the problem","Place the problem within one or more areas of law."),
      ("Spot threshold and jurisdiction issues","Identify limitations, standing, and forum questions.")),
   ("RNlaIvo9pK9MgOcwKug5on","near"), ex("m03","m05","m08","m09")),
 ],
 "SK-LP-02": [
  ("Write a legal analysis memorandum",
   "Produce a written IRAC analysis of a legal question in a matter.",
   "analysis","M2",
   st(("State the issue and rule","Frame the question and the controlling rule."),
      ("Apply the law to the facts","Reason from rule to the matter's facts."),
      ("Reach a reasoned conclusion","State a supported conclusion and its confidence.")),
   ("R8Gc3Ce7YAbKZLOUs4pCQuA","near"), ex("m01","m03","m06")),
  ("Apply governing law to disputed facts",
   "Match the elements of the governing law to the contested facts of the matter.",
   "analysis","M2",
   st(("Extract the legal elements","Break the rule into its required elements."),
      ("Match facts to elements","Line up the facts against each element."),
      ("Weigh counterarguments","Test the analysis against the opposing view.")),
   ("R8Gc3Ce7YAbKZLOUs4pCQuA","near"), ex("m03","m07","m06")),
  ("Construct and evaluate legal arguments",
   "Build affirmative arguments and critically evaluate their strength against the opposition.",
   "evaluation","M2",
   st(("Build the affirmative argument","Assemble facts and authority into an argument."),
      ("Anticipate the opposing argument","Predict and address the counterposition."),
      ("Assess relative strength","Judge which arguments are strongest.")),
   ("R77n3Z14OcgllZTAx8sw9xZ","near"), ex("m02","m06","m03")),
  ("Synthesize authority into a working rule",
   "Reconcile multiple, possibly conflicting, authorities into a usable statement of the rule.",
   "synthesis","M2",
   st(("Reconcile conflicting authority","Harmonize or distinguish competing sources."),
      ("Derive a rule statement","State the synthesized rule for the file.")),
   ("R8Gc3Ce7YAbKZLOUs4pCQuA","near"), ex("m06","m07","m10")),
 ],
 "SK-LP-03": [
  ("Identify the controlling substantive rules",
   "Locate and state the substantive rules that govern the matter.",
   "comprehension","M1",
   st(("Locate governing statutes and code","Find the controlling primary law."),
      ("Identify controlling elements","State the substantive elements at issue.")),
   ("R7TB5AJsngthjmL6KsEht3G","near"), ex("m06","m07","m10")),
  ("Analyze the elements of a claim or defense",
   "Break a claim or defense into its prima facie elements and available defenses.",
   "analysis","M2",
   st(("Enumerate prima facie elements","List each element of the claim."),
      ("Identify affirmative defenses","List defenses that may defeat the claim.")),
   ("R8Gc3Ce7YAbKZLOUs4pCQuA","near"), ex("m03","m07","m06")),
  ("Apply area-specific doctrine to the facts",
   "Apply the doctrine of the matter's practice area to its particular facts, noting jurisdictional variation.",
   "application","M2",
   st(("Apply doctrine to matter facts","Work the doctrine through the facts."),
      ("Note jurisdictional variations","Flag differences across the real-tier states.")),
   ("R8Gc3Ce7YAbKZLOUs4pCQuA","near"), ex("m10","m06","m07")),
 ],
 "SK-LP-04": [
  ("Determine the governing procedural framework",
   "Identify the court, forum, and rule set that will govern the matter's procedure.",
   "comprehension","M1",
   st(("Identify court, forum, and rules","Determine the tribunal and its rules."),
      ("Determine filing deadlines","Calendar the controlling deadlines.")),
   ("R79LjWBKHCdmQKEW6zPY8Co","parent"), ex("m03","m05","m06")),
  ("Apply the rules of civil procedure to a filing",
   "Ensure a filing satisfies jurisdiction, venue, and service requirements.",
   "application","M2",
   st(("Confirm jurisdiction and venue","Verify the forum is proper."),
      ("Comply with service requirements","Effect and document proper service.")),
   ("RBgzWJQz52CrObfGXDKH1t7","near"), ex("m03","m07")),
  ("Apply the rules of evidence in preparation",
   "Assess admissibility and lay foundation for key evidence before a hearing or trial.",
   "application","M2",
   st(("Assess admissibility of exhibits","Test exhibits against the evidence rules."),
      ("Prepare foundation for key evidence","Plan how each item will be admitted.")),
   ("RBgzWJQz52CrObfGXDKH1t7","near"), ex("m03","m05")),
  ("Navigate appellate procedure",
   "Comply with appellate rules, format, and deadlines for a review proceeding.",
   "application","M3",
   st(("Confirm appellate rules and format","Meet the format and length requirements."),
      ("Calendar briefing deadlines","Track the appellate briefing schedule.")),
   ("RBKT1I6J8ce2Gs6xwwFcUTr","near"), ex("m02","m12")),
 ],
 "SK-LP-05": [
  ("Formulate a research plan",
   "Plan a library research effort: issues, sources, and search terms.",
   "application","M1",
   st(("Identify issues to research","List the questions to answer."),
      ("Select sources and search terms","Choose the tools and vocabulary.")),
   ("R7TB5AJsngthjmL6KsEht3G","near"), ex("m01","m06","m10")),
  ("Locate and read primary authority in print",
   "Find and read the controlling statutes and case law in library sources.",
   "application","M1",
   st(("Find controlling statutes","Locate the governing code sections."),
      ("Find on-point case law","Locate cases on the issue.")),
   ("R7TB5AJsngthjmL6KsEht3G","near"), ex("m03","m06","m07")),
  ("Validate and update authority",
   "Confirm that authority is still good law and note subsequent history.",
   "analysis","M2",
   st(("Confirm authority is good law","Check that the source is still valid."),
      ("Note subsequent history","Record later treatment of the source.")),
   ("R7TB5AJsngthjmL6KsEht3G","near"), ex("m06","m02")),
 ],
 "SK-LP-06": [
  ("Run electronic database searches",
   "Search online legal databases with effective queries filtered to the jurisdiction.",
   "application","M1",
   st(("Build boolean and natural-language queries","Construct effective search strings."),
      ("Filter by jurisdiction","Restrict results to the governing jurisdiction.")),
   ("R7TB5AJsngthjmL6KsEht3G","near"), ex("m11","m13","m16")),
  ("Cite-check using online tools",
   "Use citator tools to verify citations and check their treatment.",
   "application","M1",
   st(("Verify citation validity","Confirm each cite exists and is accurate."),
      ("Check treatment flags","Review negative or cautionary treatment.")),
   ("R7TB5AJsngthjmL6KsEht3G","near"), ex("m12","m16")),
  ("Assemble a research trail and report",
   "Record the sources found and summarize the findings for the file.",
   "synthesis","M2",
   st(("Record sources found","Log the authorities located."),
      ("Summarize findings for the file","Write a short research summary.")),
   ("R7TB5AJsngthjmL6KsEht3G","near"), ex("m06","m07")),
 ],
 "SK-LP-07": [
  ("Conduct a client intake interview",
   "Interview a new client to elicit their story, goals, and concerns while building trust.",
   "application","M1",
   st(("Use open-ended then focused questions","Move from broad to narrow (T-funnel)."),
      ("Build rapport and trust","Establish a working relationship."),
      ("Elicit the client's goals and concerns","Capture what the client wants and fears.")),
   ("R83QdGrsILzbGVfk0bqF1kw","near"), ex(*ALL20)),
  ("Investigate and develop the facts",
   "Gather documents and witness information and assess factual gaps and inconsistencies.",
   "analysis","M2",
   st(("Gather documents and records","Collect the relevant records."),
      ("Identify and interview witnesses","Find and question fact witnesses."),
      ("Assess gaps and inconsistencies","Test the facts for holes and conflicts.")),
   ("RVZJBUCRB7cgWN1CIbeJJq","exact"), ex("m03","m06","m07","m05")),
  ("Interview fact witnesses",
   "Prepare for and conduct interviews of fact witnesses and memorialize their statements.",
   "application","M2",
   st(("Prepare a witness question outline","Plan the areas to cover."),
      ("Capture and memorialize statements","Record the witness account.")),
   ("R83QdGrsILzbGVfk0bqF1kw","near"), ex("m03","m05","m08","m09")),
  ("Build and maintain a chronology of facts",
   "Order events into a timeline and link facts to the legal elements they support.",
   "synthesis","M2",
   st(("Order events on a timeline","Place events in sequence."),
      ("Link facts to legal elements","Connect each fact to an element.")),
   ("RNlaIvo9pK9MgOcwKug5on","near"), ex("m03","m06","m02")),
 ],
 "SK-LP-08": [
  ("Deliver an opening statement or oral argument",
   "Present a structured, theme-driven oral argument and respond to questions from the bench.",
   "synthesis","M3",
   st(("Structure the argument","Organize the oral presentation."),
      ("Use theory and themes","Anchor the argument in the case theory."),
      ("Respond to questions from the bench","Handle questioning under pressure.")),
   ("RBgzWJQz52CrObfGXDKH1t7","near"), ex("m03","m05","m02","m06")),
  ("Conduct direct and cross-examination",
   "Plan and deliver direct examination to elicit the story and cross to control the witness.",
   "synthesis","M3",
   st(("Plan direct to elicit the story","Design questions that let the witness tell it."),
      ("Plan cross to control the witness","Design leading questions that limit the witness.")),
   ("RBgzWJQz52CrObfGXDKH1t7","near"), ex("m03","m05","m01")),
  ("Counsel and communicate with the client orally",
   "Explain options and confirm understanding in plain, accessible language.",
   "application","M2",
   st(("Explain options in plain language","Translate legal choices for the client."),
      ("Confirm client understanding","Check that the client understood.")),
   ("R9p98NogvwJk1MnJgYzuiZd","near"), ex("m04","m05","m09")),
  ("Present a negotiation position orally",
   "State interests and positions and make and respond to offers in real time.",
   "application","M2",
   st(("State interests and positions","Present the client's goals and stance."),
      ("Make and respond to offers","Advance and react to proposals.")),
   ("RDa0Qu7gpJEIhpO0REOfc1p","near"), ex("m04","m05","m06","m09")),
 ],
 "SK-LP-09": [
  ("Draft client correspondence and letters",
   "Write clear client-facing letters, including status and advice letters.",
   "application","M1",
   st(("Write an engagement or status letter","Communicate scope or progress."),
      ("Write a client advice letter","Convey advice in writing.")),
   ("RPzuFFXebWVvkhzE9KZ304","near"), ex("m01","m05","m10")),
  ("Draft correspondence to opposing counsel and the court",
   "Write professional correspondence to opposing counsel and the clerk, with proof of service.",
   "application","M2",
   st(("Draft a meet-and-confer letter","Communicate with opposing counsel."),
      ("Include proof of service","Document service on the other side.")),
   ("RPzuFFXebWVvkhzE9KZ304","near"), ex("m03","m06","m07")),
  ("Write a persuasive brief or memorandum of law",
   "Draft a persuasive brief with organized argument headings and integrated citations.",
   "synthesis","M3",
   st(("Organize argument headings","Structure the brief's point headings."),
      ("Integrate authority and record cites","Weave in citations to law and record.")),
   ("R8Gc3Ce7YAbKZLOUs4pCQuA","near"), ex("m02","m06","m12")),
  ("Edit and revise written work against a rubric",
   "Self-edit and revise a draft for clarity, concision, and page limits against the rubric.",
   "evaluation","M2",
   st(("Self-edit for clarity and concision","Tighten and clarify the prose."),
      ("Revise to meet page limits","Cut to the required length.")),
   ("R8Gc3Ce7YAbKZLOUs4pCQuA","near"), ex("m01","m03","m06")),
 ],
 "SK-LP-10": [
  ("Advise the client on options and risks",
   "Lay out the alternatives and their consequences and give a candid recommendation.",
   "analysis","M2",
   st(("Lay out alternatives and consequences","Present the realistic options."),
      ("Give a candid recommendation","Recommend a course of action.")),
   ("R9p98NogvwJk1MnJgYzuiZd","near"), ex("m04","m05","m08","m10")),
  ("Counsel on settlement versus proceeding",
   "Help the client weigh settling against proceeding, using BATNA and cost-benefit.",
   "evaluation","M3",
   st(("Evaluate the client's BATNA","Assess the best alternative to agreement."),
      ("Explain cost, benefit, and risk","Frame the tradeoffs clearly.")),
   ("R9p98NogvwJk1MnJgYzuiZd","near"), ex("m03","m06","m09","m07")),
  ("Manage client expectations and decisions",
   "Clarify that the client owns key decisions and document the advice given.",
   "application","M2",
   st(("Clarify decision ownership","Confirm the client decides."),
      ("Document the advice given","Memorialize the counseling.")),
   ("R9p98NogvwJk1MnJgYzuiZd","near"), ex("m08","m09","m10")),
  ("Counsel a distressed or vulnerable client",
   "Acknowledge emotion, rebuild focus, and coordinate with a guardian or parent where needed.",
   "application","M3",
   st(("Acknowledge emotion and rebuild focus","Respond to distress and refocus."),
      ("Coordinate with a guardian or parent","Involve a caregiver appropriately.")),
   ("R9p98NogvwJk1MnJgYzuiZd","near"), ex("m08","m09","m05")),
 ],
 "SK-LP-11": [
  ("Build client trust and rapport",
   "Earn the client's confidence through competence, preparation, and reliable follow-through.",
   "application","M1",
   st(("Demonstrate competence and preparation","Show mastery of the matter."),
      ("Communicate reliably and follow through","Do what you say, when you say it.")),
   ("R83QdGrsILzbGVfk0bqF1kw","near"), ex("m03","m05","m09")),
  ("Establish credibility with the tribunal and opposing counsel",
   "Build a reputation for candor and preparedness before the court and opposing counsel.",
   "application","M3",
   st(("Demonstrate candor and preparedness","Be straight and ready."),
      ("Honor commitments and deadlines","Keep your professional word.")),
   NONE("Establishing credibility before a tribunal is an interpersonal skill with no FOLIO service concept."),
   ex("m02","m06","m03")),
  ("Maintain the attorney-client relationship over time",
   "Keep an ongoing client informed and responsive to sustain the relationship.",
   "application","M2",
   st(("Keep the client informed","Provide regular updates."),
      ("Respond promptly to communications","Answer the client quickly.")),
   ("R9PLqqxNIpwbQxLWPoLvR5n","near"), ex("m09","m10","m19")),
 ],
 "SK-LP-12": [
  ("Conduct a new-client intake and screening",
   "Gather intake information and run a preliminary conflicts screen before accepting a client.",
   "application","M1",
   st(("Gather intake information","Collect the client and matter details."),
      ("Run a preliminary conflicts check","Screen for conflicts before engaging.")),
   ("Ry2zpiHIeLctXWkmz2CO6C","near"), ex("m03","m05","m06","m08")),
  ("Develop business and generate referrals",
   "Identify prospective clients and communicate the firm's value to win work.",
   "application","M3",
   st(("Identify prospective clients","Find likely sources of work."),
      ("Communicate the firm's value","Explain why to hire the firm.")),
   ("R81n0uoiTFQMwwdzAskqkZD","near"), ex("m01","m04")),
  ("Retain clients through service and relationship",
   "Keep clients by delivering responsive service and acting on their feedback.",
   "application","M3",
   st(("Deliver responsive service","Serve clients promptly and well."),
      ("Solicit and act on feedback","Ask for and use client feedback.")),
   ("R9PLqqxNIpwbQxLWPoLvR5n","near"), ex("m09","m10")),
 ],
 "SK-LP-13": [
  ("Prepare a strategic settlement and negotiation plan (SSNP)",
   "Plan a negotiation: interests, positions, BATNA, targets, and confidential concessions.",
   "synthesis","M2",
   st(("Identify interests, positions, and BATNA","Map the negotiation landscape."),
      ("Set target and reservation points","Fix the goal and walk-away."),
      ("Plan confidential concessions","Decide what can be traded and when.")),
   ("RDa0Qu7gpJEIhpO0REOfc1p","near"), ex("m04","m05","m06","m09")),
  ("Conduct a settlement or plea negotiation",
   "Exchange and evaluate offers using principled bargaining and document the terms reached.",
   "synthesis","M2",
   st(("Exchange and evaluate offers","Advance and assess proposals."),
      ("Use principled bargaining","Negotiate on interests, not positions."),
      ("Document the terms reached","Record the agreement.")),
   ("RDa0Qu7gpJEIhpO0REOfc1p","near"), ex("m05","m03","m06","m09")),
  ("Negotiate a transaction to agreement",
   "Reconcile competing deal terms and draft the negotiated agreement.",
   "synthesis","M2",
   st(("Reconcile competing deal terms","Bridge the parties' positions."),
      ("Draft the negotiated agreement","Reduce the deal to writing.")),
   ("RCijZPAaPw0lgYwtl8qWG67","near"), ex("m04","m14","m07","m17")),
  ("Participate in mediation",
   "Prepare a mediation position and engage the neutral to explore options.",
   "application","M2",
   st(("Prepare a mediation position or statement","Set out the client's case for the neutral."),
      ("Engage the neutral and explore options","Work with the mediator toward resolution.")),
   ("R9zgcjBBIdWMnwIR8nqsoS8","exact"), ex("m07","m17","m09","m19")),
 ],
 "SK-LP-14": [
  ("Draft pleadings to initiate or respond",
   "Draft the complaint or petition and the answer and counterclaim.",
   "application","M2",
   st(("Draft the complaint or petition","Plead the claim."),
      ("Draft the answer and counterclaim","Respond and assert counterclaims.")),
   ("RBgzWJQz52CrObfGXDKH1t7","near"), ex("m03","m07","m06")),
  ("Conduct discovery",
   "Propound and respond to written discovery and take and defend depositions.",
   "application","M2",
   st(("Draft interrogatories and document requests","Propound written discovery."),
      ("Respond to written discovery","Answer the other side's requests."),
      ("Take or defend a deposition","Conduct deposition practice.")),
   ("R9hR0S1zExtgPQTzV0LeyD3","exact"), ex("m03","m06","m07","m09")),
  ("Engage in motion practice",
   "Draft and argue a motion with supporting and opposing memoranda and a proposed order.",
   "analysis","M2",
   st(("Draft the motion and supporting memo","Write the moving papers."),
      ("Draft the opposition","Write the responsive papers."),
      ("Prepare a proposed order","Draft the order for the court.")),
   ("RBgzWJQz52CrObfGXDKH1t7","near"), ex("m06","m05","m03")),
  ("Try a case to verdict",
   "Prepare and present a trial from openings through witnesses to closings and jury instructions.",
   "synthesis","M3",
   st(("Prepare the trial notebook","Assemble the trial materials."),
      ("Present openings, witnesses, and closings","Deliver the trial presentation."),
      ("Propose jury instructions and verdict form","Draft the instructions and verdict.")),
   ("RBgzWJQz52CrObfGXDKH1t7","near"), ex("m03","m13","m05","m15")),
  ("Conduct an arbitration hearing",
   "Draft the statement of the case and present evidence to the arbitrator.",
   "synthesis","M3",
   st(("Draft the statement of the case","Prepare the arbitration statement."),
      ("Present evidence to the arbitrator","Try the matter before the arbitrator.")),
   ("RXO2u2WBkUUcEG1jczLIcA","exact"), ex("m01","m11","m07")),
  ("Handle an appeal",
   "Frame issues and standard of review, write the appellate brief, and argue the appeal.",
   "synthesis","M3",
   st(("Frame issues and standard of review","Define the appellate questions."),
      ("Write the appellate brief","Draft the brief."),
      ("Present oral argument","Argue before the court.")),
   ("RBKT1I6J8ce2Gs6xwwFcUTr","exact"), ex("m02","m12")),
 ],
 "SK-LP-15": [
  ("Manage the matter file and documents",
   "Organize the case file, exhibits, and document versions for a matter.",
   "application","M1",
   st(("Organize the case file and exhibits","Keep the file orderly."),
      ("Track key documents and versions","Control document versions.")),
   ("RDCyhgZrQPH8YgEp0C1Jtvb","near"), ex("m03","m06","m07")),
  ("Calendar deadlines and manage the docket",
   "Docket court and discovery deadlines with reminders and buffers.",
   "application","M1",
   st(("Docket court and discovery deadlines","Enter every controlling date."),
      ("Set reminders and buffers","Add lead-time alerts.")),
   ("RDCyhgZrQPH8YgEp0C1Jtvb","near"), ex("m03","m05","m06")),
  ("Coordinate the two-person firm's division of labor",
   "Allocate tasks between the two partners and balance the workload realistically.",
   "application","M2",
   st(("Allocate tasks between partners","Split the work fairly."),
      ("Balance the workload realistically","Avoid overloading either partner.")),
   ("R8Ol92hxELxA00tCrZWrQDK","near"), ex("m01","m03","m09")),
  ("Track time and prepare for billing",
   "Record contemporaneous time entries and categorize the work for the invoice.",
   "application","M2",
   st(("Record contemporaneous time entries","Log time as work is done."),
      ("Categorize work for the invoice","Code entries for billing.")),
   ("RDDsp1sHEeFoxTQnJeEyPPh","near"), ex("m01","m06","m07")),
 ],
 "SK-LP-16": [
  ("Identify and resolve conflicts of interest",
   "Run a conflicts check and evaluate waiver or withdrawal where a conflict appears.",
   "analysis","M1",
   st(("Run a conflicts check","Screen the matter for conflicts."),
      ("Evaluate waiver or withdrawal options","Decide how to cure a conflict.")),
   ("RDpaN9hLN8jGKrKBnQ2kPbN","near"), ex("m02","m04","m06")),
  ("Safeguard client confidences and privilege",
   "Protect privileged material and avoid improper disclosures.",
   "application","M2",
   st(("Protect privileged material","Keep privileged content secure."),
      ("Avoid improper disclosures","Prevent inadvertent waiver.")),
   ("Ru8IZ0c4FPR4BSj4MyRQT3","near"), ex("m06","m02","m16")),
  ("Comply with the rules of professional conduct",
   "Honor duties such as the no-contact rule and candor to the tribunal.",
   "application","M2",
   st(("Honor the no-contact (Rule 4.2) rule","Avoid contact with represented parties."),
      ("Maintain candor to the tribunal","Be truthful with the court.")),
   NONE("FOLIO has no rules-of-professional-conduct service concept; compliance is embodied in the attorney-discipline matters as a Dispute Service."),
   ex("m02","m12")),
  ("Handle client funds and trust duties ethically",
   "Keep client funds in trust, avoid commingling, and reconcile the trust ledger.",
   "application","M2",
   st(("Keep client funds in trust","Segregate client money."),
      ("Avoid commingling and reconcile the ledger","Balance and separate the trust account.")),
   ("RgV0GDy1p5PQCXleadL2hV","near"), ex("m02","m12","m09","m19")),
 ],
 "SK-LP-17": [
  ("Draft transactional agreements",
   "Draft a purchase or sale agreement and its protective clauses.",
   "synthesis","M2",
   st(("Draft a purchase or sale agreement","Reduce the deal to a contract."),
      ("Draft protective clauses","Add easement, warranty, or similar protections.")),
   ("RCijZPAaPw0lgYwtl8qWG67","near"), ex("m04","m14","m07","m17")),
  ("Draft a will and estate-planning documents",
   "Draft a will to the client's instructions with an explanatory client letter.",
   "synthesis","M2",
   st(("Draft the will per client instructions","Prepare the testamentary document."),
      ("Draft the explanatory client letter","Explain the will to the client.")),
   ("R9bCaZjWhV1f2PgQiwzAkDm","near"), ex("m10","m20")),
  ("Draft an engagement letter and fee agreement",
   "Draft the engagement letter stating scope, fee structure, and required billing terms.",
   "application","M1",
   st(("State scope and fee structure","Define the engagement and fee."),
      ("Include required billing terms","Add the billing and payment terms.")),
   ("RBwgCSrndWQriCjEO63iWbx","exact"), ex("m03","m05","m09","m10")),
  ("Draft pleadings and litigation documents",
   "Draft pleadings, motions, affidavits, and proposed orders.",
   "application","M2",
   st(("Draft pleadings and motions","Prepare the litigation papers."),
      ("Draft affidavits and proposed orders","Prepare supporting documents.")),
   ("R9JHul9FKmkFasxXopMmrpq","near"), ex("m03","m06","m05")),
  ("Draft discovery instruments",
   "Draft interrogatories and requests for production.",
   "application","M2",
   st(("Draft interrogatories","Prepare written questions."),
      ("Draft requests for production","Prepare document requests.")),
   ("RBNIcoXeUcBXVeFvlXkbGaS","near"), ex("m03","m06","m07")),
 ],
 # ---------------- Practice Management ----------------
 "SK-PM-01": [
  ("Select and structure the fee arrangement",
   "Match the fee type to the matter and document it in the agreement.",
   "application","M2",
   st(("Match fee type to matter","Choose hourly, flat, contingency, or retainer."),
      ("Document the fee in the agreement","Record the fee terms.")),
   ("R9RWwdyMp12ldMe0u2EZjhO","near"), ex("m03","m05","m09","m10")),
  ("Record time and expenses",
   "Keep contemporaneous time sheets and capture disbursements.",
   "application","M2",
   st(("Keep contemporaneous time sheets","Log time as it is spent."),
      ("Capture disbursements and expenses","Record costs advanced.")),
   ("RDDsp1sHEeFoxTQnJeEyPPh","near"), ex("m01","m06","m07")),
  ("Prepare a client billing statement",
   "Compile time entries into an invoice, compute the balance due, and enclose a cover letter.",
   "application","M2",
   st(("Compile time entries into an invoice","Assemble the billing statement."),
      ("Compute fees, expenses, and balance due","Total the amounts owed."),
      ("Enclose a billing cover letter","Send the statement with a letter.")),
   ("RgV0GDy1p5PQCXleadL2hV","near"), ex("m01","m04","m07","m16")),
  ("Manage retainers and trust accounting",
   "Deposit funds to trust and reconcile the trust ledger so it is never negative.",
   "application","M3",
   st(("Deposit funds to trust","Place advance funds in trust."),
      ("Reconcile the trust ledger","Balance the trust account.")),
   ("RgV0GDy1p5PQCXleadL2hV","near"), ex("m09","m19","m02","m12")),
 ],
 "SK-PM-02": [
  ("Define staffing needs and roles",
   "Identify the support-staff roles the firm needs and write role expectations.",
   "comprehension","M3",
   st(("Identify support-staff roles","Determine needed positions."),
      ("Write role expectations","Define what each role does.")),
   ("RBJy3KoNBYSWUf4xdARMhYt","parent"), []),
  ("Supervise and delegate to non-lawyer staff",
   "Delegate appropriate tasks and supervise staff for competence and ethical compliance.",
   "application","M3",
   st(("Delegate appropriate tasks","Assign suitable work to staff."),
      ("Supervise for competence and ethics","Oversee quality and conduct.")),
   ("R8Ol92hxELxA00tCrZWrQDK","near"), ex("m12")),
  ("Onboard and manage support staff",
   "Train new staff on firm systems and set performance expectations.",
   "application","M3",
   st(("Train on firm systems","Bring staff up to speed."),
      ("Set performance expectations","Define success for the role.")),
   ("RBJy3KoNBYSWUf4xdARMhYt","parent"), []),
 ],
 "SK-PM-03": [
  ("Plan the firm's startup capitalization",
   "Estimate startup costs and identify sources of capital for the firm.",
   "comprehension","M3",
   st(("Estimate startup costs","Project what launch will cost."),
      ("Identify capital sources","Find funding options.")),
   NONE("FOLIO has no firm-capitalization concept; this is a business-formation planning task with no service equivalent."), []),
  ("Manage cash flow and reserves",
   "Forecast cash flow and maintain operating reserves.",
   "application","M3",
   st(("Forecast cash flow","Project inflows and outflows."),
      ("Maintain operating reserves","Hold a cushion for lean months.")),
   ("RW7P6EPg4NMe8dhyO52jzl","near"), []),
  ("Evaluate investment in tools and growth",
   "Assess the return on a purchase and prioritize investments.",
   "evaluation","M3",
   st(("Assess ROI of a purchase","Weigh cost against benefit."),
      ("Prioritize investments","Rank competing investments.")),
   NONE("Firm investment decisions have no FOLIO service equivalent; nearest adjacent is Budgeting Management."), []),
 ],
 "SK-PM-04": [
  ("Plan and scope a matter as a project",
   "Define deliverables and milestones and estimate the effort and time required.",
   "application","M2",
   st(("Define deliverables and milestones","Set the outputs and checkpoints."),
      ("Estimate effort and time","Project the work required.")),
   ("R9ScBHXlUfa31SlBnKmDarJ","near"), ex("m03","m06","m09")),
  ("Manage personal and firm time",
   "Prioritize tasks and track billable versus non-billable time.",
   "application","M1",
   st(("Prioritize tasks","Order work by importance and deadline."),
      ("Track billable versus non-billable time","Separate the two categories.")),
   ("RDDsp1sHEeFoxTQnJeEyPPh","near"), ex("m01","m07")),
  ("Improve process efficiency",
   "Identify bottlenecks and standardize repeatable work.",
   "analysis","M3",
   st(("Identify bottlenecks","Find where work stalls."),
      ("Standardize repeatable work","Templatize recurring tasks.")),
   ("R9ScBHXlUfa31SlBnKmDarJ","near"), []),
 ],
 "SK-PM-05": [
  ("Prepare a matter or firm budget",
   "Estimate fees and costs and set a budget baseline.",
   "application","M3",
   st(("Estimate fees and costs","Project the numbers."),
      ("Set a budget baseline","Fix the plan of record.")),
   ("RW7P6EPg4NMe8dhyO52jzl","near"), ex("m03","m01")),
  ("Allocate resources across the docket",
   "Match resources to matter priority and rebalance as needs change.",
   "application","M3",
   st(("Match resources to matter priority","Assign effort to what matters most."),
      ("Rebalance as needs change","Adjust allocations over time.")),
   ("R8Ol92hxELxA00tCrZWrQDK","near"), []),
  ("Monitor budget versus actuals",
   "Compare actuals to budget and adjust the plan.",
   "evaluation","M3",
   st(("Compare actuals to budget","Track variance."),
      ("Adjust the plan","Revise the budget as needed.")),
   ("RW7P6EPg4NMe8dhyO52jzl","near"), []),
 ],
 "SK-PM-06": [
  ("Develop a marketing and client-development plan",
   "Define the target clients and niche and choose outreach channels.",
   "synthesis","M3",
   st(("Define target clients and niche","Pick who to serve."),
      ("Choose outreach channels","Select how to reach them.")),
   ("R81n0uoiTFQMwwdzAskqkZD","near"), []),
  ("Build the firm's brand and network",
   "Cultivate referral relationships and maintain a professional presence.",
   "application","M3",
   st(("Cultivate referral relationships","Grow a referral network."),
      ("Maintain a professional presence","Keep a credible public profile.")),
   ("R81n0uoiTFQMwwdzAskqkZD","near"), []),
  ("Prepare pitches and proposals",
   "Scope a prospective engagement and present fees and value.",
   "application","M3",
   st(("Scope the prospective engagement","Define the proposed work."),
      ("Present fees and value","Explain price and worth.")),
   ("R8MT8gA5gTVF6rbGoWbxYJw","near"), []),
 ],
 "SK-PM-07": [
  ("Adopt and use practice technology",
   "Select case-management tools and use document and billing software.",
   "application","M2",
   st(("Select case-management tools","Choose the firm's systems."),
      ("Use document and billing software","Operate the core tools.")),
   NONE("Selecting and using practice technology has no FOLIO service concept; FOLIO's technology concepts are narrow tooling categories."), []),
  ("Manage electronic communications and records",
   "Organize email and files and retain records per policy.",
   "application","M2",
   st(("Organize email and files","Keep communications orderly."),
      ("Retain records per policy","Apply the retention schedule.")),
   ("R9MD9rwRADsqy2eadoz5R8L","near"), []),
  ("Protect data security and client information",
   "Secure devices and accounts and guard against data loss.",
   "application","M3",
   st(("Secure devices and accounts","Lock down access."),
      ("Guard against data loss","Back up and protect data.")),
   ("R97IEI6qOls8PLTHVBTaE0w","near"), ex("m06","m16")),
 ],
 "SK-PM-08": [
  ("Establish firm governance and decision rules",
   "Define partner decision rights and document firm policies.",
   "comprehension","M3",
   st(("Define partner decision rights","Decide who decides what."),
      ("Document firm policies","Write the firm's rules.")),
   ("RBJy3KoNBYSWUf4xdARMhYt","parent"), []),
  ("Set long-range strategy",
   "Set multi-year goals and align resources to the strategy.",
   "synthesis","M3",
   st(("Set multi-year goals","Define where the firm is going."),
      ("Align resources to strategy","Point resources at the goals.")),
   ("RBJy3KoNBYSWUf4xdARMhYt","parent"), []),
  ("Make and review firm-level decisions",
   "Weigh options and risks and review outcomes to adjust.",
   "evaluation","M3",
   st(("Weigh options and risks","Assess the choices."),
      ("Review outcomes and adjust","Learn from results.")),
   ("Ru8IZ0c4FPR4BSj4MyRQT3","near"), []),
 ],
 "SK-PM-09": [
  ("Communicate effectively within the firm",
   "Hold regular partner check-ins and give constructive feedback.",
   "application","M2",
   st(("Hold regular partner check-ins","Keep the partnership aligned."),
      ("Give constructive feedback","Coach and correct helpfully.")),
   ("RBJy3KoNBYSWUf4xdARMhYt","parent"), ex("m01")),
  ("Manage partner and staff relationships",
   "Align on expectations and address friction early.",
   "application","M3",
   st(("Align on expectations","Set shared expectations."),
      ("Address friction early","Handle tension before it grows.")),
   ("RBJy3KoNBYSWUf4xdARMhYt","parent"), []),
  ("Resolve internal conflict",
   "Surface disagreements constructively and reach a workable resolution.",
   "application","M3",
   st(("Surface disagreements constructively","Bring conflict into the open."),
      ("Reach a workable resolution","Settle on a path forward.")),
   ("Ru8IZ0c4FPR4BSj4MyRQT3","near"), []),
 ],
 # ---------------- AI-era extension skills ----------------
 "SK-LP-18": [
  ("Formulate an effective legal prompt",
   "Write a prompt that specifies role, task, context, and the governing facts and constraints.",
   "application","M1",
   st(("Specify role, task, and context","Frame the request precisely."),
      ("Supply governing facts and constraints","Give the model what it needs.")),
   NONE("Prompt formulation is an AI-era skill not modeled in FOLIO."), ex("m03","m05")),
  ("Iterate and refine prompts",
   "Diagnose weak outputs and revise the prompt to improve results.",
   "application","M2",
   st(("Diagnose weak outputs","Identify what went wrong."),
      ("Revise the prompt and re-test","Adjust and try again.")),
   NONE("Prompt iteration is an AI-era skill not modeled in FOLIO."), ex("m06")),
  ("Build reusable prompt templates for tasks",
   "Template recurring prompts for memos or interviews and document their usage and limits.",
   "synthesis","M3",
   st(("Template a memo or interview prompt","Create a reusable prompt."),
      ("Document usage and limits","Note when and how to use it.")),
   NONE("Prompt templating is an AI-era skill not modeled in FOLIO."), []),
 ],
 "SK-LP-19": [
  ("Verify AI-asserted facts against the record",
   "Cross-check AI claims to the case file and flag unsupported assertions.",
   "evaluation","M2",
   st(("Cross-check claims to the case file","Verify against source facts."),
      ("Flag unsupported assertions","Mark what cannot be verified.")),
   NONE("AI-output fact verification is an AI-era skill not modeled in FOLIO."), ex("m03","m07")),
  ("Check AI-provided citations and authority",
   "Confirm each AI citation exists and says what the output claims.",
   "evaluation","M2",
   st(("Confirm each citation exists","Verify the source is real."),
      ("Confirm it says what is claimed","Read the source for support.")),
   NONE("AI citation checking is an AI-era skill adjacent to Legal Research but with no dedicated FOLIO concept."), ex("m06")),
  ("Detect and correct hallucinations and errors",
   "Identify fabricated content and correct it before relying on or filing the work.",
   "evaluation","M3",
   st(("Identify fabricated content","Spot invented facts or law."),
      ("Correct before relying or filing","Fix errors before use.")),
   NONE("Hallucination detection is an AI-era skill not modeled in FOLIO."), []),
 ],
 "SK-LP-20": [
  ("Decide which tasks to delegate to AI versus retain",
   "Assess task risk and reversibility and keep judgment-heavy work with the human.",
   "evaluation","M3",
   st(("Assess task risk and reversibility","Judge what is safe to delegate."),
      ("Keep judgment-heavy work human","Retain the hard calls.")),
   NONE("Centaur delegation judgment is an AI-era skill not modeled in FOLIO."), []),
  ("Integrate AI first-pass into the revise-and-repeat loop",
   "Obtain an AI first-pass critique and revise the work against the rubric.",
   "synthesis","M3",
   st(("Get an AI first-pass critique","Run the draft through AI."),
      ("Revise against the rubric","Improve using the rubric.")),
   NONE("The AI-plus-human revise loop is an AI-era workflow not modeled in FOLIO."), ex("m03","m06")),
  ("Supervise AI as you would a junior",
   "Set the assignment clearly and review AI work before it leaves the firm.",
   "application","M3",
   st(("Set the assignment clearly","Give the model a clear brief."),
      ("Review before it leaves the firm","Check the work before use.")),
   NONE("Supervising AI output is an AI-era skill not modeled in FOLIO."), []),
 ],
 "SK-LP-21": [
  ("Avoid disclosing client confidences to AI tools",
   "Strip or avoid identifiers and use only approved tools when working with AI.",
   "application","M2",
   st(("Strip or avoid PII and identifiers","Keep client identity out of prompts."),
      ("Use approved tools only","Restrict to vetted systems.")),
   NONE("Confidentiality in AI use is an AI-era skill adjacent to Risk Management but with no dedicated FOLIO concept."), ex("m06","m02")),
  ("Preserve privilege when using AI",
   "Assess waiver risk and document the safeguards taken.",
   "application","M3",
   st(("Assess waiver risk","Judge the privilege exposure."),
      ("Document safeguards","Record the protections used.")),
   NONE("Privilege preservation in AI use is an AI-era skill not modeled in FOLIO."), []),
 ],
 "SK-PM-10": [
  ("Maintain clean matter data for AI use",
   "Keep the record complete and current and separate facts from work product.",
   "application","M2",
   st(("Keep the record complete and current","Maintain accurate matter data."),
      ("Separate facts from work product","Distinguish source facts from analysis.")),
   NONE("AI-oriented matter-data hygiene is an AI-era skill adjacent to Records Management but with no dedicated FOLIO concept."), []),
  ("Govern AI use across the firm",
   "Set an AI-use policy and log the tools and data flows.",
   "synthesis","M3",
   st(("Set an AI-use policy","Define acceptable AI use."),
      ("Log tools and data flows","Track what is used and how.")),
   NONE("Firm-wide AI governance is an AI-era skill not modeled in FOLIO."), []),
  ("Manage retention and deletion of AI interactions",
   "Retain AI interactions per policy and delete transient prompts and outputs.",
   "application","M3",
   st(("Retain per policy","Keep what policy requires."),
      ("Delete transient prompts and outputs","Purge ephemeral data.")),
   NONE("Retention of AI interactions is an AI-era skill adjacent to Records Management but with no dedicated FOLIO concept."), []),
 ],
}

# ---------------------------------------------------------------------------
# BUILD
# ---------------------------------------------------------------------------
def folio_obj(dec):
    iri, second = dec
    return {"iri": iri, "mapping_confidence": second}

def load_existing(name):
    path = os.path.join(HERE, name)
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None


identity_doc = load_existing("taxonomy-identities.json")
if not identity_doc:
    raise RuntimeError("taxonomy-identities.json is required; identities may not be positional")
identity_by_seed = {}
for record in identity_doc["tasks"]:
    key = (record["skill_id"], record["seed_name"])
    if key in identity_by_seed:
        raise RuntimeError("duplicate task identity seed: %r" % (key,))
    identity_by_seed[key] = record

old_skills_doc = load_existing("skills.json") or {}
old_tasks_doc = load_existing("tasks.json") or {}
old_cross_doc = load_existing("folio-crosswalk.json") or {}
old_skills = {item["id"]: item for item in old_skills_doc.get("skills", [])}
old_tasks = {item["id"]: item for item in old_tasks_doc.get("tasks", [])}
old_cross_skills = {item["id"]: item for item in old_cross_doc.get("skills", [])}
old_cross_tasks = {item["id"]: item for item in old_cross_doc.get("tasks", [])}


def preserve(obj, old, fields):
    """Overlay only explicitly authored wording; structure stays generated."""
    for field in fields:
        if isinstance(old.get(field), str):
            obj[field] = old[field]


skills_out, cross_skills = [], []
skill_ids = set()
for (sid,name,alt,cat,ext,folio,survey,note) in SKILLS:
    skill_ids.add(sid)
    obj = {"id":sid,"schema_version":SV,"@id":f"{BASE}/skill/{sid}","name":name,
           "category":cat,"extension":ext}
    if alt: obj["alt_name"]=alt
    if folio: obj["folio"]=folio_obj(folio)
    else: obj["no_folio_equivalent"]=True
    if survey: obj["survey"]=survey
    preserve(obj, old_skills.get(sid, {}), ("name", "alt_name"))
    skills_out.append(obj)
    # crosswalk skill entry
    if folio:
        lbl,br = C[folio[0]]
        cross_skills.append({"id":sid,"folio_iri":folio[0],"folio_label":lbl,
                             "branch":br,"mapping_confidence":folio[1]})
    else:
        cross_skills.append({"id":sid,"no_folio_equivalent":True,"note":note})
    preserve(cross_skills[-1], old_cross_skills.get(sid, {}), ("note",))

tasks_out, cross_tasks = [], []
for (sid,name,alt,cat,ext,folio,survey,note) in SKILLS:  # preserve skill order
    for t in TASKS[sid]:
        tname,tdesc,bloom,module,subs,tfolio,refs = t
        identity = identity_by_seed.get((sid, tname))
        if not identity:
            raise RuntimeError("task lacks reviewed literal identity: %s / %s" % (sid, tname))
        tid = identity["id"]
        if len(identity["subtasks"]) != len(subs):
            raise RuntimeError("subtask identity count changed for %s" % tid)
        subtasks=[]
        old_subtasks = {item["id"]: item for item in old_tasks.get(tid, {}).get("subtasks", [])}
        for identity_sub, (sn,sd) in zip(identity["subtasks"], subs):
            if identity_sub["seed_name"] != sn:
                raise RuntimeError("subtask identity reassigned for %s: %s" % (tid, sn))
            subtask = {"id":identity_sub["id"],"name":sn,"description":sd}
            preserve(subtask, old_subtasks.get(subtask["id"], {}), ("name", "description"))
            subtasks.append(subtask)
        obj={"id":tid,"schema_version":SV,"@id":f"{BASE}/task/{tid}","skill_id":sid,
             "name":tname,"description":tdesc,"bloom_level":bloom,"module":module,
             "subtasks":subtasks}
        if tfolio[0]!="NONE":
            obj["folio"]=folio_obj(tfolio)
            lbl,br=C[tfolio[0]]
            cross_tasks.append({"id":tid,"folio_iri":tfolio[0],"folio_label":lbl,
                                "branch":br,"mapping_confidence":tfolio[1]})
        else:
            obj["no_folio_equivalent"]=True
            cross_tasks.append({"id":tid,"no_folio_equivalent":True,"note":tfolio[1]})
        if refs: obj["exercise_refs"]=refs
        preserve(obj, old_tasks.get(tid, {}), ("name", "description"))
        tasks_out.append(obj)
        preserve(cross_tasks[-1], old_cross_tasks.get(tid, {}), ("note",))

skills_doc={"schema_version":SV,"@id":f"{BASE}/taxonomy/skills","spine_version":SV,
    "description":"Sonsteng's 17 Legal Practice + 9 Law Practice Management skills (exact survey names, both phrasings preserved) plus a clearly-marked AI-era extension set. Survey importance/preparedness from reliable tables in docs/research/skills-survey.md (Table 4 management-importance percentages deliberately omitted as unreliable).",
    "surveyed_count":26,"extension_count":sum(1 for s in SKILLS if s[4]),
    "skills":skills_out}
tasks_doc={"schema_version":SV,"@id":f"{BASE}/taxonomy/tasks","spine_version":SV,
    "description":"Primary-task -> subtask decomposition for each of the 26 surveyed skills and 5 AI-era extension skills, grounded in the master-outline deliverable set and standard practice-of-law task inventories. exercise_refs point to manifest matters that exercise the task.",
    "task_count":len(tasks_out),"tasks":tasks_out}
cross_doc={"schema_version":SV,"@id":f"{BASE}/taxonomy/folio-crosswalk",
    "verified_at":"2026-07-17","source":"folio MCP live retrieval",
    "description":"FOLIO crosswalk for every skill and every primary task. Each entry either records a verified FOLIO concept {folio_iri, folio_label (as retrieved), branch, mapping_confidence in exact|near|parent} or declares no_folio_equivalent with a note. This snapshot is the offline source of truth; the ship gate validates against it, not live MCP.",
    "skills":cross_skills,"tasks":cross_tasks}

preserve(skills_doc, old_skills_doc, ("description",))
preserve(tasks_doc, old_tasks_doc, ("description",))
preserve(cross_doc, old_cross_doc, ("description",))

used_identity_ids = {task["id"] for task in tasks_out}
declared_identity_ids = {task["id"] for task in identity_doc["tasks"]}
if used_identity_ids != declared_identity_ids:
    raise RuntimeError("identity manifest contains missing/reassigned task IDs")

for fn,doc in [("skills.json",skills_doc),("tasks.json",tasks_doc),("folio-crosswalk.json",cross_doc)]:
    with open(os.path.join(HERE,fn),"w") as f:
        json.dump(doc,f,indent=2,ensure_ascii=False)
        f.write("\n")

# ---------------------------------------------------------------------------
# VALIDATE
# ---------------------------------------------------------------------------
import jsonschema
sd=os.path.join(HERE,"..","schemas")
skill_schema=json.load(open(os.path.join(sd,"skill.schema.json")))
task_schema=json.load(open(os.path.join(sd,"task.schema.json")))
errs=0
for s in skills_out:
    for e in jsonschema.Draft202012Validator(skill_schema).iter_errors(s):
        errs+=1; print("SKILL ERR",s["id"],e.message)
for t in tasks_out:
    for e in jsonschema.Draft202012Validator(task_schema).iter_errors(t):
        errs+=1; print("TASK ERR",t["id"],e.message)

# invariants
manifest=json.load(open(os.path.join(HERE,"..","matters","manifest.json")))
valid_matters={m["id"] for m in manifest["matters"]}
for t in tasks_out:
    for r in t.get("exercise_refs",[]):
        mm=r[:-3]
        if mm not in valid_matters: errs+=1; print("BAD REF",t["id"],r)
# unique ids
ids=[t["id"] for t in tasks_out]
assert len(ids)==len(set(ids)),"dup task ids"
subids=[s["id"] for t in tasks_out for s in t["subtasks"]]
assert len(subids)==len(set(subids)),"dup subtask ids"

# stats
def conf_counts(entries):
    c={"exact":0,"near":0,"parent":0,"none":0}
    for e in entries:
        if e.get("no_folio_equivalent"): c["none"]+=1
        else: c[e["mapping_confidence"]]+=1
    return c
cs=conf_counts(cross_skills); ct=conf_counts(cross_tasks)
tot={k:cs[k]+ct[k] for k in cs}
nsub=sum(len(t["subtasks"]) for t in tasks_out)
print("\n=== VALIDATION:", "GREEN" if errs==0 else f"{errs} ERRORS ===")
print(f"skills={len(skills_out)} (surveyed 26 + ext {skills_doc['extension_count']})  tasks={len(tasks_out)}  subtasks={nsub}")
print("skill mapping:",cs)
print("task  mapping:",ct)
print("TOTAL mapping:",tot)
sys.exit(1 if errs else 0)
