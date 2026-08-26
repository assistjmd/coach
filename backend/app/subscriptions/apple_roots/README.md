# Apple root certificates

Verification of Apple's signed transactions and notifications checks the JWS
certificate chain up to one of these roots. Without them nothing can be
verified, and `VerificationError` is raised rather than falling back to trusting
the payload.

Download from https://www.apple.com/certificateauthority/ and place the `.cer`
files here:

- AppleRootCA-G3.cer  (the one Apple currently signs App Store JWS with)
- AppleRootCA-G2.cer
- AppleIncRootCertificate.cer

They are public certificates, not secrets, and are committed deliberately so a
deploy cannot land without them.
