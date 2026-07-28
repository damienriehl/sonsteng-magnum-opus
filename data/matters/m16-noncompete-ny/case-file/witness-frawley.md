# Witness Statement — Dominic Frawley {#b:3d7f19f2}

**Witness:** Dominic Frawley, Senior Engineer, Hudson Valley Biometrics, Inc.
**Taken by:** Ellingboe & Ravndal LLP
**Date:** April 14, 2026 {#b:35992a38}

My name is Dominic Frawley. I'm a senior engineer at Hudson Valley Biometrics, and I still work there. I've been on the engineering team for about five years, and for most of that time I worked alongside Priya Iyer on the matching engine. I'm giving this statement voluntarily because I don't think what's being said about Priya — that she walked out with some secret algorithm in her head — is a fair description of how any of this actually works. {#b:2cf78335}

Let me start with what the "matching engine" is, because I think that's where the whole case goes sideways. It's not a magic box. It's a pipeline: you take an image or a video of someone, you run it through a neural network that turns it into a list of numbers — we call that an embedding — and then you compare those numbers against a database to find the closest matches. Every piece of that is standard computer-vision work. The network architectures we use come out of published academic papers. The training methods are in the literature. A lot of the actual code sits on top of open-source libraries that anyone can download for free. If you put ten qualified vision engineers in a room and described the problem, most of them would reach for the same tools. {#b:dbf5fef8}

What's genuinely ours is the tuning — the specific settings, the training data we've collected, the thousand small judgment calls that make our version work well. That part has real value. But that's not something you carry out in your head as a single secret. It's spread across a lot of people and a lot of files, and Priya didn't take any of those files. I know, because I still have access to all of them, and nothing's missing. {#b:8937bd5c}

I also want to be honest about how we handled the code, because I keep hearing it described as locked down, and it wasn't. The source lived in shared repositories that the whole engineering team could read and check out. We talked about the architecture openly in engineering meetings — Priya used to present on it. There was no separate password wall around the "secret" parts, no compartment where only a few people could look. That's just how a fast-moving startup builds software. I'm not criticizing the company; I'm telling you it wasn't run like a classified program. {#b:d8b0b159}

Priya was the best engineer I've worked with, and she was straight with people. When leadership passed her over last year, a lot of us thought it was a mistake, and I wasn't surprised she left. I don't know anything about what she emailed herself or what she took in a notebook — I can't speak to that. I can only tell you how the engine was built and how the code was treated while I was sitting three desks away from her, which is: like ordinary engineering work resting on public foundations, not like a vault. {#b:670b48e4}

I understand I may be asked to say this under oath, and I'm prepared to. {#b:2440dabc}

/s/ Dominic Frawley {#b:138b0762}
