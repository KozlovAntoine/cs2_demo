# Prototypes visuels · Utilitaires CS2

Double-cliquer sur `Ouvrir les prototypes.cmd` pour ouvrir `index.html` dans le navigateur par défaut. Autre possibilité : lancer `python -m http.server 38481 --bind 127.0.0.1 --directory prototypes` depuis la racine puis ouvrir http://127.0.0.1:38481.

Trois directions : A Tactique, B Studio et C Focus. Chaque direction propose la vidéothèque, le nouvel export et ses états de traitement, l’éditeur de zones et une planche de composants. Les variantes changent la composition, la densité, la typographie et les couleurs.

Tout est autonome dans ce dossier. Les vidéos et radars sont des copies de quelques médias locaux existants. Les filtres, la sélection, la lecture vidéo et les favoris sont interactifs. Les imports, rendus, suppressions et modifications de zones sont simulés et ne contactent pas le serveur du site. Les données de démonstration et les favoris sont réinitialisés au rechargement.

L’éditeur illustre la sélection et le renommage des zones, avec un tracé de démonstration. Le zoom fonctionne, sans reproduire l’ensemble de la géométrie de l’éditeur réel. La page Composants permet aussi d’examiner les confirmations de suppression et l’attribution d’une zone.

Les sources existantes dans `app/`, `gallery/` et les scripts Python ne sont pas modifiées. Aucun prototype n’est déployé. Le choix visuel et ses éventuels ajustements précèdent l’implémentation.

## Tactique · révision retenue

La direction A utilise désormais une palette noire et orange cuivré, sans les accents citron de la première proposition. Sur ordinateur, sa vidéothèque adopte une vue unique : lecteur à gauche sur 75 % de la largeur de travail, carte puis lancers et console dans une colonne de 25 % à droite. Les filtres restent en haut, avec les barres de navigation conservées et un en-tête compact. La colonne de droite défile indépendamment. Il n’y a pas de mode fullscreen distinct dans le prototype.

Sur mobile, le lecteur passe en premier, puis la carte, les lancers et la console. Les variantes B et C restent disponibles comme références. Ouvrir directement `index.html#v=a&p=library` pour la révision de Tactique.
