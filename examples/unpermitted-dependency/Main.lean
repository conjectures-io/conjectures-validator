theorem target : type_of% VerifierFixtures.direct := by
  exact VerifierFixtures.propAnswer.mp
    (VerifierFixtures.propAnswer.mpr True.intro)
