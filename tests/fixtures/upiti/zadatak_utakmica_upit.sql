SELECT Utakmica.Datum
FROM Utakmica, Fudbaler, Igrao
WHERE Utakmica.IDUta = Igrao.IDUta AND Fudbaler.IDFud = Igrao.IDFud AND Fudbaler.Ime = 'Nemanja Vidic';
