# Description of the "Matching Engine" (Alleged Trade Secret) — Exhibit m16.exh.005

*This exhibit is a plain-language description prepared for the file, drawn from the parties' submissions and from engineer interviews, of the system Hudson Valley Biometrics identifies as the trade secret at the center of its motion. No source code is reproduced. The description is offered so the reader can evaluate the protectability and inevitable-disclosure questions; it states no legal conclusions.*

---

## What Hudson Valley calls "the matching engine"

Hudson Valley Biometrics's flagship product converts video or images of a person's face or walking gait into a compact list of numbers — an "embedding" — and then compares that embedding against a stored database to find the closest matches. The company calls the software that does this its "matching engine." In its motion papers, Hudson Valley describes the engine as "the culmination of years of proprietary research and investment" and "the crown jewel of the Company," and it identifies Ms. Iyer's knowledge of the engine as the trade secret it seeks to protect.

## The components, in plain terms

- **The embedding model.** A neural network that turns an image into a numeric vector. The network architecture is a variant of designs published in the academic computer-vision literature.
- **The training method.** The model is trained using loss functions and data-augmentation techniques that are described in published papers and implemented in widely used open-source machine-learning libraries.
- **The matching step.** Once an embedding is produced, the system finds the nearest entries in the database using standard nearest-neighbor search techniques.
- **The proprietary layer.** What Hudson Valley developed itself sits on top of these public foundations: the specific hyperparameters chosen through experimentation, the curated and labeled training data the company assembled, and the accumulated engineering judgment about what configurations work best for its customers' conditions.

## How the engine was maintained internally (per engineer interviews)

- The source code resided in shared internal repositories accessible to the full engineering team.
- The architecture was presented and discussed openly in company engineering meetings.
- There was no separate password barrier or "need to know" compartment isolating the engine's core.
- No single document consistently marked "trade secret" described the engine as a whole; sensitivity was understood by custom rather than by formal marking.

## What is disputed

Hudson Valley contends that Ms. Iyer, as the engine's technical lead, carries its trade secrets in her memory and will inevitably use or disclose them in any competing role. Ms. Iyer contends that the engine's methods are public, that what is genuinely proprietary (the tuning and data) she did not take in any form, that the company's off-boarding forensic review found no company materials on her devices, and that general skill and knowledge she developed over her career are hers to use.
