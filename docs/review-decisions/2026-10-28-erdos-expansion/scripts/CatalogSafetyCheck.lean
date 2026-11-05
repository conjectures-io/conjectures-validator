import CatalogExtractor

unsafe def checkCatalogSafety : IO Unit := do
  let result ← withImports #[`TestFixtures.CatalogSafety]
    (extractCatalog #[`TestFixtures.CatalogSafety])
  IO.println result.compress

#eval checkCatalogSafety
